"""
prices.py
---------
Fetches live / latest prices from Yahoo Finance for a list of tickers
and merges them into the unified holdings DataFrame.

Behaviour
---------
- Bulk-downloads all tickers in one API call (fast).
- Applies ticker mapping (e.g. BRK/B -> BRK-B) before querying.
- For tickers Yahoo cannot find, falls back to the custodian file price
  already present in the holdings DataFrame.
- Recalculates market_value = quantity * live_price after merge.
- Returns an enriched DataFrame with two extra columns:
    price_source  : "live" | "custodian"
    prev_close    : previous day closing price (for daily P&L)
"""

import pandas as pd
import yfinance as yf
from datetime import datetime, date


# ---------------------------------------------------------------------------
# Ticker normalisation map  (custodian ticker -> Yahoo ticker)
# ---------------------------------------------------------------------------

TICKER_MAP = {
    "BRK/B": "BRK-B",   # Berkshire Hathaway Class B
}


def _to_yahoo(ticker: str) -> str:
    """Convert a custodian ticker to its Yahoo Finance equivalent."""
    return TICKER_MAP.get(ticker.strip(), ticker.strip())


def _from_yahoo(yahoo_ticker: str) -> str:
    """Reverse-map a Yahoo ticker back to custodian format."""
    reverse = {v: k for k, v in TICKER_MAP.items()}
    return reverse.get(yahoo_ticker, yahoo_ticker)


# ---------------------------------------------------------------------------
# Core fetch
# ---------------------------------------------------------------------------

def fetch_prices(tickers: list[str]) -> pd.DataFrame:
    """
    Fetch latest price and previous close for a list of tickers.

    Parameters
    ----------
    tickers : list of custodian-format ticker strings

    Returns
    -------
    pd.DataFrame with columns:
        ticker        - custodian format
        live_price    - latest available price from Yahoo
        prev_close    - previous trading day close
        price_time    - timestamp of the price
        price_source  - always "live" for rows returned here
    """
    if not tickers:
        return pd.DataFrame(columns=["ticker", "live_price", "prev_close",
                                     "price_time", "price_source"])

    # Build Yahoo tickers list (deduplicated)
    unique = list(dict.fromkeys(tickers))  # preserve order, remove dupes
    yahoo_tickers = [_to_yahoo(t) for t in unique]

    print(f"  Fetching prices for {len(yahoo_tickers)} tickers from Yahoo Finance...")

    # Bulk download — period="5d" gives us enough history for prev_close
    # auto_adjust=True adjusts for splits/dividends
    try:
        raw = yf.download(
            tickers=yahoo_tickers,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        print(f"  [WARNING] Yahoo Finance download failed: {e}")
        return pd.DataFrame(columns=["ticker", "live_price", "prev_close",
                                     "price_time", "price_source"])

    results = []

    for yahoo_t in yahoo_tickers:
        custodian_t = _from_yahoo(yahoo_t)
        try:
            # Single ticker returns flat columns; multiple returns multi-level
            if len(yahoo_tickers) > 1:
                close_series = raw["Close"][yahoo_t].dropna()
            else:
                close_series = raw["Close"].dropna()

            if close_series.empty:
                raise ValueError("No data returned")

            live_price  = float(close_series.iloc[-1])
            prev_close  = float(close_series.iloc[-2]) if len(close_series) >= 2 else live_price
            price_time  = close_series.index[-1].to_pydatetime()

            results.append({
                "ticker"      : custodian_t,
                "live_price"  : round(live_price, 4),
                "prev_close"  : round(prev_close, 4),
                "price_time"  : price_time,
                "price_source": "live",
            })

        except Exception:
            # Ticker not found or no data — will fall back to custodian price
            results.append({
                "ticker"      : custodian_t,
                "live_price"  : None,
                "prev_close"  : None,
                "price_time"  : None,
                "price_source": "failed",
            })

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Merge into holdings
# ---------------------------------------------------------------------------

def enrich_with_prices(holdings: pd.DataFrame) -> pd.DataFrame:
    """
    Fetch live prices and merge them into the holdings DataFrame.

    - Replaces current_price with live_price where available.
    - Falls back to custodian file price if Yahoo lookup fails.
    - Recalculates market_value = quantity * current_price.
    - Adds columns: prev_close, price_source, daily_pnl, daily_pnl_pct.

    Parameters
    ----------
    holdings : DataFrame from ingestion.load_holdings()

    Returns
    -------
    Enriched DataFrame with same unified schema + extra columns.
    """
    all_tickers = holdings["ticker"].dropna().unique().tolist()

    price_df = fetch_prices(all_tickers)

    # Merge on ticker
    enriched = holdings.merge(price_df, on="ticker", how="left")

    # Ensure live_price is float (avoids dtype conflict)
    enriched["live_price"] = pd.to_numeric(enriched["live_price"], errors="coerce")

    # Fill live_price — use custodian price as fallback
    mask_failed = enriched["live_price"].isna()
    enriched["current_price"] = enriched["live_price"].combine_first(enriched["current_price"])
    enriched.loc[mask_failed, "price_source"] = "custodian"

    # Recalculate market value with live price
    enriched["market_value"] = (enriched["quantity"] * enriched["current_price"]).round(2)

    # Daily P&L  (vs previous close)
    enriched["daily_pnl"] = (
        (enriched["current_price"] - enriched["prev_close"]) * enriched["quantity"]
    ).round(2)

    enriched["daily_pnl_pct"] = (
        (enriched["current_price"] - enriched["prev_close"]) / enriched["prev_close"] * 100
    ).round(4)

    # Drop helper column
    enriched = enriched.drop(columns=["live_price"])

    # Unrealized P&L = market value - cost basis
    enriched["unrealized_pnl"] = (enriched["market_value"] - enriched["cost_basis"]).round(2)

    return enriched.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Mar'26 TD return calculator
# ---------------------------------------------------------------------------

# Base portfolio values as of March 1, 2026 (start of period).
# UPDATE THESE when the user supplies the actual March 1 values.
# Set to None to show "—" on the dashboard until values are provided.
MAR26_BASE_VALUES: dict[str, float | None] = {
    "Endowment Fund": None,   # e.g. 14_500_000.0
    "Longhorn Fund":  None,   # e.g. 13_200_000.0
}


def calculate_period_returns(portfolio_df: pd.DataFrame) -> dict:
    """
    Calculate Mar'26 TD return for each fund.

    Formula: (current_market_value - mar1_base_value) / mar1_base_value * 100

    Base values are set in MAR26_BASE_VALUES above.
    Returns {'mtd': <float>} per fund, or {'mtd': None} if base not yet set.
    """
    results = {}
    for fund_name, grp in portfolio_df.groupby("fund_name"):
        current_mv = grp["market_value"].sum()
        base       = MAR26_BASE_VALUES.get(fund_name)
        if base and base > 0:
            mtd_return = round((current_mv - base) / base * 100, 2)
        else:
            mtd_return = None
        results[fund_name] = {"mtd": mtd_return}
    return results


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from src.ingestion import load_holdings

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", "{:,.2f}".format)

    print("\n" + "=" * 70)
    print("  PRICE FETCHER - TEST")
    print("=" * 70 + "\n")

    holdings = load_holdings()
    enriched = enrich_with_prices(holdings)

    # Summary by fund
    for fund, grp in enriched.groupby("fund_name"):
        total_mv        = grp["market_value"].sum()
        total_cost      = grp["cost_basis"].sum()
        total_unrealized= grp["unrealized_pnl"].sum()
        total_daily     = grp["daily_pnl"].sum()
        live_count      = (grp["price_source"] == "live").sum()
        fallback_count  = (grp["price_source"] == "custodian").sum()

        print(f"\n{'-'*70}")
        print(f"  {fund}  |  {len(grp)} positions  "
              f"|  {live_count} live  |  {fallback_count} fallback")
        print(f"{'-'*70}")

        display_cols = ["ticker", "security_name", "quantity",
                        "current_price", "market_value", "cost_basis",
                        "unrealized_pnl", "daily_pnl", "daily_pnl_pct",
                        "price_source"]
        print(grp[display_cols].to_string(index=False))

        print(f"\n  Total Market Value   : ${total_mv:>15,.2f}")
        print(f"  Total Cost Basis     : ${total_cost:>15,.2f}")
        print(f"  Unrealized P&L       : ${total_unrealized:>15,.2f}")
        print(f"  Daily P&L            : ${total_daily:>15,.2f}")

    # Failed tickers
    failed = enriched[enriched["price_source"] == "custodian"]["ticker"].tolist()
    if failed:
        print(f"\n  [FALLBACK to custodian price]: {', '.join(failed)}")
    else:
        print("\n  All tickers resolved via Yahoo Finance.")

    print("\n" + "=" * 70 + "\n")
