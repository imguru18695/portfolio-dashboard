"""
data_manager.py
---------------
Central data manager for the portfolio dashboard.

Responsibilities
----------------
1. HOLDINGS CACHE
   - Reads custodian files once and caches the result.
   - Watches the Custodian Holdings folder for new daily file drops.
   - Re-ingests automatically when a newer file is detected.

2. PRICE CACHE
   - Fetches live prices from Yahoo Finance and caches them.
   - Refreshes prices on a configurable interval (default: 5 minutes).
   - During market close / weekends, falls back to last known prices.

3. COMBINED DATA
   - Returns a single enriched DataFrame (holdings + live prices).
   - Thread-safe via a simple lock — safe for Streamlit's concurrent requests.

Usage
-----
    from src.data_manager import DataManager

    dm = DataManager()
    df = dm.get_portfolio()           # enriched holdings, auto-refreshed
    meta = dm.get_status()            # last refresh times, staleness flags
    dm.force_refresh()                # manual full refresh
"""

import os
import time
import threading
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

try:
    from src.ingestion import load_holdings, HOLDINGS_FOLDER
    from src.prices   import enrich_with_prices
except ModuleNotFoundError:
    from ingestion import load_holdings, HOLDINGS_FOLDER
    from prices    import enrich_with_prices


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PRICE_REFRESH_INTERVAL_SECONDS = 300      # 5 minutes between price refreshes
MARKET_TZ = ZoneInfo("America/New_York")

# US market hours  (Eastern)
MARKET_OPEN_HOUR  = 9
MARKET_OPEN_MIN   = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN  = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_et() -> datetime:
    return datetime.now(MARKET_TZ)


def _is_market_hours() -> bool:
    """Return True if current ET time is within regular trading hours Mon-Fri."""
    now = _now_et()
    if now.weekday() >= 5:          # Saturday = 5, Sunday = 6
        return False
    market_open  = now.replace(hour=MARKET_OPEN_HOUR,  minute=MARKET_OPEN_MIN,  second=0)
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0)
    return market_open <= now <= market_close


def _folder_fingerprint(folder: Path) -> str:
    """
    Create a fingerprint of the holdings folder based on filenames
    and their last-modified timestamps. Changes when a new file is dropped.
    """
    entries = []
    try:
        for f in sorted(folder.iterdir()):
            if f.is_file():
                mtime = os.path.getmtime(f)
                entries.append(f"{f.name}:{mtime}")
    except Exception:
        pass
    combined = "|".join(entries)
    return hashlib.md5(combined.encode()).hexdigest()


# ---------------------------------------------------------------------------
# DataManager
# ---------------------------------------------------------------------------

class DataManager:
    """
    Singleton-friendly data manager.

    Typical use: instantiate once at app startup, then call get_portfolio()
    on every dashboard refresh — it handles cache logic internally.
    """

    def __init__(
        self,
        holdings_folder: Path = HOLDINGS_FOLDER,
        price_refresh_interval: int = PRICE_REFRESH_INTERVAL_SECONDS,
    ):
        self._folder             = holdings_folder
        self._price_interval     = price_refresh_interval
        self._lock               = threading.Lock()

        # Holdings cache
        self._holdings_df        : pd.DataFrame | None = None
        self._holdings_fingerprint: str                 = ""
        self._holdings_loaded_at : datetime | None      = None

        # Enriched (prices merged) cache
        self._portfolio_df       : pd.DataFrame | None = None
        self._prices_refreshed_at: datetime | None      = None

        # Status tracking
        self._last_error         : str                  = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_portfolio(self) -> pd.DataFrame:
        """
        Return the latest enriched portfolio DataFrame.

        Logic:
          - If the holdings folder has changed  → re-ingest + re-price.
          - If price cache is stale              → re-price only.
          - Otherwise                            → return cached result.
        """
        with self._lock:
            holdings_changed = self._holdings_changed()
            prices_stale     = self._prices_stale()

            if holdings_changed:
                print(f"[DataManager] New holdings detected — re-ingesting...")
                self._refresh_holdings()
                self._refresh_prices()

            elif prices_stale:
                print(f"[DataManager] Price cache stale — refreshing prices...")
                self._refresh_prices()

            else:
                print(f"[DataManager] Serving from cache "
                      f"(prices age: {self._prices_age_seconds():.0f}s)")

            return self._portfolio_df.copy() if self._portfolio_df is not None else pd.DataFrame()

    def force_refresh(self) -> pd.DataFrame:
        """Force a full re-ingest + re-price regardless of cache state."""
        with self._lock:
            print("[DataManager] Force refresh requested.")
            self._refresh_holdings()
            self._refresh_prices()
            return self._portfolio_df.copy() if self._portfolio_df is not None else pd.DataFrame()

    def get_status(self) -> dict:
        """Return a status dict for display in the dashboard sidebar."""
        now = datetime.now()

        holdings_age_str = "Never"
        if self._holdings_loaded_at:
            secs = (now - self._holdings_loaded_at).total_seconds()
            holdings_age_str = _format_age(secs)

        prices_age_str = "Never"
        if self._prices_refreshed_at:
            secs = self._prices_age_seconds()
            prices_age_str = _format_age(secs)

        next_refresh_secs = max(
            0,
            self._price_interval - self._prices_age_seconds()
        ) if self._prices_refreshed_at else 0

        return {
            "holdings_loaded_at"    : self._holdings_loaded_at,
            "holdings_age"          : holdings_age_str,
            "prices_refreshed_at"   : self._prices_refreshed_at,
            "prices_age"            : prices_age_str,
            "next_price_refresh_in" : f"{next_refresh_secs:.0f}s",
            "is_market_hours"       : _is_market_hours(),
            "market_status"         : "Open" if _is_market_hours() else "Closed",
            "total_positions"       : len(self._portfolio_df) if self._portfolio_df is not None else 0,
            "funds"                 : list(self._portfolio_df["fund_name"].unique()) if self._portfolio_df is not None else [],
            "last_error"            : self._last_error,
        }

    # ------------------------------------------------------------------
    # Internal: staleness checks
    # ------------------------------------------------------------------

    def _holdings_changed(self) -> bool:
        """Return True if the holdings folder contents have changed."""
        if self._holdings_df is None:
            return True    # first load
        current_fp = _folder_fingerprint(self._folder)
        return current_fp != self._holdings_fingerprint

    def _prices_stale(self) -> bool:
        """Return True if prices need refreshing."""
        if self._portfolio_df is None or self._prices_refreshed_at is None:
            return True
        return self._prices_age_seconds() >= self._price_interval

    def _prices_age_seconds(self) -> float:
        """Seconds since the last price refresh."""
        if self._prices_refreshed_at is None:
            return float("inf")
        return (datetime.now() - self._prices_refreshed_at).total_seconds()

    # ------------------------------------------------------------------
    # Internal: refresh actions
    # ------------------------------------------------------------------

    def _refresh_holdings(self):
        """Re-read custodian files and update the holdings cache."""
        try:
            self._holdings_df          = load_holdings(self._folder)
            self._holdings_fingerprint = _folder_fingerprint(self._folder)
            self._holdings_loaded_at   = datetime.now()          # local, tz-naive
            self._last_error           = ""
            print(f"[DataManager] Holdings loaded: "
                  f"{len(self._holdings_df)} positions across "
                  f"{self._holdings_df['fund_name'].nunique()} fund(s).")
        except Exception as e:
            self._last_error = str(e)
            print(f"[DataManager] ERROR loading holdings: {e}")

    def _refresh_prices(self):
        """Fetch live prices and merge into holdings."""
        if self._holdings_df is None or self._holdings_df.empty:
            print("[DataManager] Skipping price refresh — no holdings loaded.")
            return
        try:
            self._portfolio_df        = enrich_with_prices(self._holdings_df)
            self._prices_refreshed_at = datetime.now()           # local, tz-naive
            self._last_error          = ""

            live_count     = (self._portfolio_df["price_source"] == "live").sum()
            fallback_count = (self._portfolio_df["price_source"] == "custodian").sum()
            print(f"[DataManager] Prices refreshed: "
                  f"{live_count} live, {fallback_count} fallback.")
        except Exception as e:
            self._last_error = str(e)
            print(f"[DataManager] ERROR refreshing prices: {e}")
            # Keep stale data rather than returning nothing
            if self._portfolio_df is None:
                self._portfolio_df = self._holdings_df.copy()


# ---------------------------------------------------------------------------
# Helpers for display
# ---------------------------------------------------------------------------

def _format_age(seconds: float) -> str:
    """Convert seconds to a human-readable age string."""
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    elif seconds < 3600:
        return f"{seconds/60:.0f}m ago"
    else:
        return f"{seconds/3600:.1f}h ago"


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  DATA MANAGER - TEST")
    print("=" * 70 + "\n")

    dm = DataManager(price_refresh_interval=15)   # 15s for testing

    # --- First call: cold cache, should ingest + fetch prices ---
    print("[TEST 1] First call (cold cache)...")
    df1 = dm.get_portfolio()
    s1  = dm.get_status()
    print(f"  Positions: {s1['total_positions']} | Funds: {s1['funds']}")
    print(f"  Holdings age: {s1['holdings_age']}")
    print(f"  Prices age:   {s1['prices_age']}")
    print(f"  Market: {s1['market_status']}")
    print(f"  Next refresh in: {s1['next_price_refresh_in']}")
    print()

    # --- Second call: should serve from cache ---
    print("[TEST 2] Immediate second call (should use cache)...")
    df2 = dm.get_portfolio()
    s2  = dm.get_status()
    print(f"  Prices age: {s2['prices_age']}")
    print()

    # --- Wait for price cache to expire (15s) ---
    print("[TEST 3] Waiting 16s for price cache to expire...")
    time.sleep(16)
    df3 = dm.get_portfolio()
    s3  = dm.get_status()
    print(f"  Prices age after re-fetch: {s3['prices_age']}")
    print()

    # --- Force refresh ---
    print("[TEST 4] Force refresh...")
    df4 = dm.force_refresh()
    s4  = dm.get_status()
    print(f"  Positions after force refresh: {s4['total_positions']}")
    print()

    # --- Summary stats from live data ---
    print("[TEST 5] Portfolio summary from live data:")
    for fund, grp in df4.groupby("fund_name"):
        mv    = grp["market_value"].sum()
        upnl  = grp["unrealized_pnl"].sum()
        dpnl  = grp["daily_pnl"].sum()
        live  = (grp["price_source"] == "live").sum()
        print(f"  {fund}: MV=${mv:>15,.2f}  |  Unrealized=${upnl:>12,.2f}"
              f"  |  Daily P&L=${dpnl:>11,.2f}  |  {live}/{len(grp)} live")

    print("\n" + "=" * 70 + "\n")
