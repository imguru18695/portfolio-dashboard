"""
benchmark.py
------------
Fetches and computes benchmark data from yfinance for both funds.

Endowment  → blended multi-asset benchmark (9 ETFs, fixed weights)
Longhorn   → S&P 500 Total Return Index (^SP500TR)
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Benchmark definitions
# ---------------------------------------------------------------------------

ENDOWMENT_BENCHMARK = [
    {"ticker": "SPY",  "weight": 0.25, "name": "SPDR S&P 500 ETF Trust",                    "group": "Equity"},
    {"ticker": "ACWX", "weight": 0.22, "name": "iShares MSCI ACWI ex US ETF",               "group": "Equity"},
    {"ticker": "IJH",  "weight": 0.05, "name": "iShares Core S&P Mid-Cap ETF",              "group": "Equity"},
    {"ticker": "IJR",  "weight": 0.05, "name": "iShares Core S&P Small-Cap ETF",            "group": "Equity"},
    {"ticker": "USIG", "weight": 0.15, "name": "iShares Broad USD Inv Grade Corp Bond ETF", "group": "Fixed Income"},
    {"ticker": "IEI",  "weight": 0.10, "name": "iShares 3-7 Year Treasury Bond ETF",        "group": "Fixed Income"},
    {"ticker": "AGG",  "weight": 0.08, "name": "iShares Core US Aggregate Bond ETF",        "group": "Fixed Income"},
    {"ticker": "BIL",  "weight": 0.05, "name": "SPDR Bloomberg 1-3 Month T-Bill ETF",       "group": "Fixed Income"},
    {"ticker": "IYR",  "weight": 0.05, "name": "iShares US Real Estate ETF",                "group": "Real Estate"},
]

LONGHORN_BENCHMARK_TICKER = "^SP500TR"

# Dashboard start date — Feb 28 is the base (last trading day before Mar 1, 2026)
BASE_DATE = "2026-02-28"

# Chart label dates — must match CHART_LABELS in dashboard_v2.html
CHART_DATES = [
    "2026-03-01", "2026-03-06", "2026-03-13", "2026-03-20",
    "2026-04-01", "2026-04-10", "2026-04-17", "2026-04-28",
    "2026-05-07", "2026-05-15", "2026-05-21", "2026-05-27",
    "2026-06-03", "2026-06-10", "2026-06-17", "2026-06-24",
]

# Weekly dates used by the analytics rolling-vol chart — must match rollLabels in dashboard_v2.html
ANALYTICS_ROLL_DATES_6M = [
    "2026-03-08", "2026-03-15", "2026-03-22", "2026-04-05",
    "2026-04-12", "2026-04-19", "2026-05-03", "2026-05-10",
    "2026-05-17", "2026-05-24", "2026-05-27",
    "2026-06-03", "2026-06-10", "2026-06-17", "2026-06-24",
]

# Must match rollLabels3m in dashboard_v2.html
ANALYTICS_ROLL_DATES_3M = [
    "2026-04-19", "2026-05-03", "2026-05-10",
    "2026-05-17", "2026-05-24", "2026-05-27",
    "2026-06-03", "2026-06-10", "2026-06-17", "2026-06-24",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_closes(
    tickers: list[str], start: str, end: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (closes, real_mask). closes is forward-filled; real_mask is True where the original data was real."""
    end = end or (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"]
    else:
        closes = raw[["Close"]] if "Close" in raw.columns else raw
        if len(tickers) == 1:
            closes.columns = tickers
    real_mask = closes.notna()
    closes = closes.ffill(limit=5)
    return closes, real_mask


def _align_to_dates(series: pd.Series, date_list: list[str]) -> list[float | None]:
    """
    For each date in date_list find the closest available trading day on or before that date.
    Returns a list of floats (or None if no data available yet).
    """
    result = []
    idx = series.index.normalize()
    for d in date_list:
        target = pd.Timestamp(d)
        available = idx[idx <= target]
        if available.empty:
            result.append(None)
        else:
            result.append(round(float(series[available[-1]]), 6))
    return result


def _align_to_chart_dates(series: pd.Series) -> list[float | None]:
    return _align_to_dates(series, CHART_DATES)


def _rolling_vol_annualised(returns: pd.Series, window: int = 21) -> pd.Series:
    return returns.rolling(window).std() * np.sqrt(252) * 100


def _period_return(prices: pd.Series, days_back: int, business_days: bool = False) -> float | None:
    """Return % change over the last N days from the series.

    Set business_days=True for the 1D period so weekends don't inflate the
    return (e.g. Monday's 1D would otherwise span Friday→Monday = 3 cal days).
    """
    end_price = prices.iloc[-1]
    if business_days:
        cutoff = prices.index[-1] - pd.tseries.offsets.BDay(days_back)
    else:
        cutoff = prices.index[-1] - pd.Timedelta(days=days_back)
    available = prices[prices.index <= cutoff]
    if available.empty:
        return None
    return round((end_price / available.iloc[-1] - 1) * 100, 2)


def _ytd_return(prices: pd.Series, year: int = 2026) -> float | None:
    year_start = prices[prices.index >= pd.Timestamp(f"{year}-01-01")]
    if year_start.empty:
        return None
    return round((prices.iloc[-1] / year_start.iloc[0] - 1) * 100, 2)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_benchmark_data(fund: str) -> dict:
    """
    Returns all benchmark data for the given fund in one payload:
      - returns:      {1d, 5d, 1m, 3m}  as % strings with sign
      - cumulative:   list of 12 floats indexed to 1.0 at Feb 28, 2026
      - rolling_vol:  list of 12 floats (annualised %, 21D rolling), None where insufficient data
      - composition:  list of component dicts with live price, 1D chg, YTD (Endowment only)
    """
    fund = fund.lower()
    fetch_start = "2026-01-01"   # enough for 63D rolling vol from Mar 1

    if fund == "endowment":
        return _endowment_benchmark(fetch_start)
    elif fund == "longhorn":
        return _longhorn_benchmark(fetch_start)
    else:
        raise ValueError(f"Unknown fund: {fund}")


def _consecutive_bdays(idx: pd.DatetimeIndex) -> bool:
    """Return True if the last two entries are consecutive business days."""
    if len(idx) < 2:
        return False
    return idx[-1] == idx[-2] + pd.tseries.offsets.BDay(1)


def _blended_series(closes: pd.DataFrame, weights: dict[str, float], base_date: str) -> pd.Series:
    """Compute a blended price series indexed to 1.0 at base_date.

    Each component is divided by its closing price on or before base_date so
    that the stated weights are exact at the base date (not drifted from an
    earlier normalisation point).
    """
    base_ts = pd.Timestamp(base_date)
    norm_factors = {}
    for t in weights:
        col = closes[t].dropna()
        prior = col[col.index <= base_ts]
        norm_factors[t] = float(prior.iloc[-1]) if not prior.empty else float(col.iloc[0])
    normalised = pd.DataFrame({t: closes[t] / norm_factors[t] for t in weights})
    blend = sum(normalised[t] * w for t, w in weights.items())
    return blend


def _endowment_benchmark(fetch_start: str) -> dict:
    tickers = [c["ticker"] for c in ENDOWMENT_BENCHMARK]
    weights = {c["ticker"]: c["weight"] for c in ENDOWMENT_BENCHMARK}

    closes, real_mask = _fetch_closes(tickers, start=fetch_start)

    # Drop any tickers that failed to fetch
    available = [t for t in tickers if t in closes.columns and closes[t].notna().any()]
    if not available:
        raise RuntimeError("Could not fetch any benchmark component prices")

    # Re-normalise weights to available tickers
    total_w = sum(weights[t] for t in available)
    adj_weights = {t: weights[t] / total_w for t in available}

    # blend is already indexed to 1.0 at BASE_DATE by _blended_series
    blend = _blended_series(closes[available], adj_weights, BASE_DATE)
    indexed = blend

    # Daily returns of the blend
    blend_returns = blend.pct_change().dropna()

    # Align to chart dates
    cumulative = _align_to_chart_dates(indexed)

    # Rolling 21D vol — aligned to all date sets
    roll_vol = _rolling_vol_annualised(blend_returns)
    rolling_vol         = _align_to_chart_dates(roll_vol)
    analytics_benchvol  = _align_to_dates(roll_vol, ANALYTICS_ROLL_DATES_6M)
    analytics_benchvol3m= _align_to_dates(roll_vol, ANALYTICS_ROLL_DATES_3M)

    # Period returns
    returns = {
        "1d": _period_return(blend, 1, business_days=True),
        "5d": _period_return(blend, 7),
        "1m": _period_return(blend, 31),
        "3m": _period_return(blend, 92),
    }

    # Composition — live price, 1D chg, YTD for each component
    composition = []
    for comp in ENDOWMENT_BENCHMARK:
        t = comp["ticker"]
        if t not in closes.columns:
            continue
        s = closes[t].dropna()
        if s.empty:
            continue
        price      = round(float(s.iloc[-1]), 2)
        last_real  = bool(real_mask[t].iloc[-1]) if t in real_mask.columns else True
        chg_1d     = round(float((s.iloc[-1] / s.iloc[-2] - 1) * 100), 2) if (_consecutive_bdays(s.index) and last_real) else None
        ytd        = _ytd_return(s)
        composition.append({
            "ticker": t,
            "name":   comp["name"],
            "group":  comp["group"],
            "weight": adj_weights[t],
            "price":  price,
            "chg1d":  chg_1d,
            "ytd":    ytd,
        })

    return {
        "fund":              "endowment",
        "benchmark":         "Dynamic Multi-Asset Blend",
        "returns":           returns,
        "cumulative":        cumulative,
        "rolling_vol":       rolling_vol,
        "analytics_benchvol":   analytics_benchvol,
        "analytics_benchvol3m": analytics_benchvol3m,
        "composition":       composition,
    }


def _longhorn_benchmark(fetch_start: str) -> dict:
    closes, _ = _fetch_closes([LONGHORN_BENCHMARK_TICKER], start=fetch_start)

    if LONGHORN_BENCHMARK_TICKER not in closes.columns or closes[LONGHORN_BENCHMARK_TICKER].dropna().empty:
        # Fallback to ^GSPC if TR index unavailable
        closes, _ = _fetch_closes(["^GSPC"], start=fetch_start)
        ticker_used = "^GSPC"
        bench_name  = "S&P 500 Index"
    else:
        ticker_used = LONGHORN_BENCHMARK_TICKER
        bench_name  = "S&P 500 Total Return Index"

    if ticker_used not in closes.columns or closes[ticker_used].dropna().empty:
        raise RuntimeError(
            f"Could not fetch benchmark data for Longhorn: both {LONGHORN_BENCHMARK_TICKER} "
            f"and {ticker_used} returned no data. Check network connectivity or Yahoo Finance availability."
        )

    s = closes[ticker_used].dropna()

    # Index to Feb 28 base
    base_available = s[s.index <= pd.Timestamp(BASE_DATE)]
    base_val = base_available.iloc[-1] if not base_available.empty else s.iloc[0]
    indexed = s / base_val

    daily_returns = s.pct_change().dropna()
    roll_vol      = _rolling_vol_annualised(daily_returns)

    cumulative          = _align_to_chart_dates(indexed)
    rolling_vol         = _align_to_chart_dates(roll_vol)
    analytics_benchvol  = _align_to_dates(roll_vol, ANALYTICS_ROLL_DATES_6M)
    analytics_benchvol3m= _align_to_dates(roll_vol, ANALYTICS_ROLL_DATES_3M)

    returns = {
        "1d": _period_return(s, 1, business_days=True),
        "5d": _period_return(s, 7),
        "1m": _period_return(s, 31),
        "3m": _period_return(s, 92),
    }

    return {
        "fund":              "longhorn",
        "benchmark":         bench_name,
        "returns":           returns,
        "cumulative":        cumulative,
        "rolling_vol":       rolling_vol,
        "analytics_benchvol":   analytics_benchvol,
        "analytics_benchvol3m": analytics_benchvol3m,
        "composition":       [],
    }
