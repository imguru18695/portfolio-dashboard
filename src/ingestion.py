"""
ingestion.py
------------
Reads custodian holdings files for both funds, maps them to a unified schema,
and returns clean DataFrames ready for the dashboard.

Unified Schema
--------------
fund_name      : str   - "Endowment Fund" or "Longhorn Fund"
as_of_date     : date  - Valuation date extracted from filename
ticker         : str   - Exchange ticker symbol
security_name  : str   - Full security description
quantity       : float - Number of shares / par value
current_price  : float - Mark / base price as of valuation date
market_value   : float - Total market value in USD
cost_basis     : float - Total cost basis in USD (if available)
weight_pct     : float - % of total portfolio NAV
"""

import os
import re
import pandas as pd
from datetime import datetime, date
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOLDINGS_FOLDER = Path(__file__).parent.parent / "Custodian Holdings"

ENDOWMENT_PATTERN = re.compile(
    r"^flex\..+\.Holdings\.(\d{8})\.\d{8}\.csv$", re.IGNORECASE
)
# Longhorn files may come in multiple naming formats from the custodian
LONGHORN_PATTERNS = [
    re.compile(r"^Asset and Accrual Detail_(\d{1,2} \w+ \d{4})\.xls$",        re.IGNORECASE),
    re.compile(r"^Custody Holdings.*_(\d{1,2} \w+ \d{4})\.xlsx?$",            re.IGNORECASE),
]

FUND_ENDOWMENT = "Endowment Fund"
FUND_LONGHORN  = "Longhorn Fund"

UNIFIED_COLUMNS = [
    "fund_name",
    "as_of_date",
    "ticker",
    "security_name",
    "quantity",
    "current_price",
    "market_value",
    "cost_basis",
    "weight_pct",
]


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _scan_all_files(folder: Path) -> dict:
    """
    Scan the holdings folder and return ALL files per fund, sorted newest first.

    Returns:
        {
            "Endowment Fund": [(path, as_of_date), ...],
            "Longhorn Fund":  [(path, as_of_date), ...],
        }
    """
    candidates = {FUND_ENDOWMENT: [], FUND_LONGHORN: []}

    for f in folder.iterdir():
        if not f.is_file():
            continue

        # --- Endowment ---
        m = ENDOWMENT_PATTERN.match(f.name)
        if m:
            dt = datetime.strptime(m.group(1), "%Y%m%d").date()
            candidates[FUND_ENDOWMENT].append((f, dt))
            continue

        # --- Longhorn ---
        for lh_pattern in LONGHORN_PATTERNS:
            m = lh_pattern.match(f.name)
            if m:
                dt = datetime.strptime(m.group(1), "%d %b %Y").date()
                candidates[FUND_LONGHORN].append((f, dt))
                break

    # Sort all files newest-first per fund
    for fund in candidates:
        candidates[fund].sort(key=lambda x: x[1], reverse=True)

    return candidates


def _discover_files(folder: Path) -> dict:
    """
    Scan the holdings folder and return the latest file path per fund.

    Returns:
        {
            "Endowment Fund": (path, as_of_date),
            "Longhorn Fund":  (path, as_of_date),
        }
    """
    all_files = _scan_all_files(folder)
    latest = {}
    for fund, files in all_files.items():
        if files:
            latest[fund] = files[0]   # (path, date)  — newest first
    return latest


def list_available_dates(folder: Path = HOLDINGS_FOLDER) -> dict:
    """
    Return all available dates per fund as ISO date strings (newest first).

    Returns:
        {
            "Endowment Fund": ["2026-04-24", ...],
            "Longhorn Fund":  ["2026-04-24", ...],
        }
    """
    all_files = _scan_all_files(folder)
    return {
        fund: [dt.isoformat() for _, dt in files]
        for fund, files in all_files.items()
    }


def load_holdings_for_date(
    folder: Path,
    fund_name: str,
    date_str: str,          # "YYYY-MM-DD"
) -> pd.DataFrame:
    """
    Load the holdings snapshot for a specific fund and date.

    Uses the custodian file's own prices (no live yfinance fetch).
    Raises ValueError if no file exists for the given fund + date.

    Returns DataFrame in the unified schema.
    """
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    all_files   = _scan_all_files(folder)
    files       = all_files.get(fund_name, [])

    match = next((p for p, d in files if d == target_date), None)
    if match is None:
        available = [d.isoformat() for _, d in files]
        raise ValueError(
            f"No {fund_name} file for {date_str}. "
            f"Available: {available or 'none'}"
        )

    if fund_name == FUND_ENDOWMENT:
        return _read_endowment(match, target_date)
    elif fund_name == FUND_LONGHORN:
        return _read_longhorn(match, target_date)
    else:
        raise ValueError(f"Unknown fund: {fund_name}")


# ---------------------------------------------------------------------------
# Endowment Fund reader
# ---------------------------------------------------------------------------

def _read_endowment(path: Path, as_of_date: date) -> pd.DataFrame:
    """
    Parse the Endowment (IBKR flex) CSV.

    Structure:
      Row 0 : Cash summary header   (8 columns)
      Row 1 : Cash summary values
      Row 2 : Holdings header        (15 columns)
      Row 3+ : Holdings data rows
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    # Holdings start at line index 2 (0-based)
    holdings_lines = lines[2:]
    from io import StringIO
    df_raw = pd.read_csv(StringIO("".join(holdings_lines)))

    # Drop rows with no Symbol (safety guard)
    df_raw = df_raw.dropna(subset=["Symbol"])
    df_raw = df_raw[df_raw["Symbol"].str.strip() != ""].reset_index(drop=True)

    df = pd.DataFrame({
        "fund_name"     : FUND_ENDOWMENT,
        "as_of_date"    : as_of_date,
        "ticker"        : df_raw["Symbol"].str.strip(),
        "security_name" : df_raw["Description"].str.strip(),
        "quantity"      : pd.to_numeric(df_raw["Quantity"],       errors="coerce"),
        "current_price" : pd.to_numeric(df_raw["MarkPrice"],      errors="coerce"),
        "market_value"  : pd.to_numeric(df_raw["PositionValue"],  errors="coerce"),
        "cost_basis"    : pd.to_numeric(df_raw["CostBasisMoney"], errors="coerce"),
        "weight_pct"    : pd.to_numeric(df_raw["PercentOfNAV"],   errors="coerce"),
    })

    return df[UNIFIED_COLUMNS].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Longhorn Fund reader
# ---------------------------------------------------------------------------

def _read_longhorn(path: Path, as_of_date: date) -> pd.DataFrame:
    """
    Parse the Longhorn (BNY Mellon custodian) XLS file.

    Structure:
      Row 0 : Column headers
      Row 1+ : Holdings data (equity + cash)

    Cash & Cash Equivalent rows are excluded from holdings
    but their market value is captured separately for weight calculation.
    """
    df_raw = pd.read_excel(path, engine="xlrd", header=0)

    # Exclude cash rows for holdings view (keep only EQUITY)
    df_eq = df_raw[df_raw["Asset Category"].str.upper() == "EQUITY"].copy()
    df_eq = df_eq.dropna(subset=["Ticker"])
    df_eq = df_eq[df_eq["Ticker"].str.strip() != ""]

    df_eq = df_eq.reset_index(drop=True)

    df = pd.DataFrame({
        "fund_name"     : FUND_LONGHORN,
        "as_of_date"    : as_of_date,
        "ticker"        : df_eq["Ticker"].str.strip(),
        "security_name" : df_eq["Security Description 1"].str.strip(),
        "quantity"      : pd.to_numeric(df_eq["Shares/Par"],        errors="coerce"),
        "current_price" : pd.to_numeric(df_eq["Base Price"],        errors="coerce"),
        "market_value"  : pd.to_numeric(df_eq["Base Market Value"], errors="coerce"),
        "cost_basis"    : pd.to_numeric(df_eq["Base Cost"],         errors="coerce"),
        "weight_pct"    : pd.to_numeric(df_eq["Percent Of Total"],  errors="coerce"),
    })

    return df[UNIFIED_COLUMNS].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_holdings(folder: Path = HOLDINGS_FOLDER) -> pd.DataFrame:
    """
    Main entry point. Scans the holdings folder, loads the latest file for
    each fund, and returns a single combined DataFrame in the unified schema.

    Returns:
        pd.DataFrame with columns: fund_name, as_of_date, ticker,
                                   security_name, quantity, current_price,
                                   market_value, cost_basis, weight_pct
    """
    latest = _discover_files(folder)

    if not latest:
        raise FileNotFoundError(f"No holdings files found in: {folder}")

    frames = []

    if FUND_ENDOWMENT in latest:
        path, dt = latest[FUND_ENDOWMENT]
        print(f"[Endowment Fund] Reading: {path.name} (as of {dt})")
        frames.append(_read_endowment(path, dt))

    if FUND_LONGHORN in latest:
        path, dt = latest[FUND_LONGHORN]
        print(f"[Longhorn Fund]  Reading: {path.name} (as of {dt})")
        frames.append(_read_longhorn(path, dt))

    combined = pd.concat(frames, ignore_index=True)
    return combined


def load_holdings_by_fund(folder: Path = HOLDINGS_FOLDER) -> dict:
    """
    Same as load_holdings() but returns a dict keyed by fund name.

    Returns:
        {
            "Endowment Fund": pd.DataFrame,
            "Longhorn Fund":  pd.DataFrame,
        }
    """
    df = load_holdings(folder)
    return {fund: grp.reset_index(drop=True)
            for fund, grp in df.groupby("fund_name")}


# ---------------------------------------------------------------------------
# Quick test (run this file directly to verify)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", "{:,.2f}".format)

    print("\n" + "=" * 70)
    print("  PORTFOLIO DASHBOARD - INGESTION TEST")
    print("=" * 70 + "\n")

    df = load_holdings()

    for fund, grp in df.groupby("fund_name"):
        total_mv = grp["market_value"].sum()
        total_cost = grp["cost_basis"].sum()
        unrealized = total_mv - total_cost

        print(f"\n{'-'*70}")
        print(f"  {fund}  |  As of: {grp['as_of_date'].iloc[0]}  |  {len(grp)} positions")
        print(f"{'-'*70}")
        print(grp.to_string(index=False))
        print(f"\n  Total Market Value : ${total_mv:>15,.2f}")
        print(f"  Total Cost Basis   : ${total_cost:>15,.2f}")
        print(f"  Unrealized P&L     : ${unrealized:>15,.2f}")

    print("\n" + "=" * 70)
    print(f"  COMBINED: {len(df)} total positions across {df['fund_name'].nunique()} funds")
    print("=" * 70 + "\n")
