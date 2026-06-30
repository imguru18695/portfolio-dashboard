"""
signals.py
----------
Compute per-ticker signals via yfinance.

Risk Attribution  : beta (calculated), annualised vol, Sharpe, SPY correlation
Momentum Signals  : vs 50D MA, vs 200D MA, % from 52W high,
                    analyst consensus, analyst coverage  — all direct from ticker.info
"""

import time
import threading
import numpy as np
import pandas as pd
import yfinance as yf

_SKIP = {"CASH", "CASH & EQUIVALENTS", ""}

# ---------------------------------------------------------------------------
# Live risk-free rate (3-month T-bill, ^IRX) — 24-hour TTL, thread-safe
# ---------------------------------------------------------------------------

_rf_annual: float | None = None
_rf_fetched_at: float | None = None
_RF_TTL_SECONDS = 86400  # 24 hours
_rf_lock = threading.Lock()


def _get_rf_annual() -> float:
    """
    Fetch the current 3-month T-bill yield (^IRX) from Yahoo Finance.
    ^IRX is quoted as an annualised percentage (e.g. 4.82 = 4.82 %).
    Cached for 24 hours under a lock to prevent thundering-herd re-fetches.
    On failure the previous value is kept; falls back to 4.25% on first failure.
    _rf_fetched_at is set on both success and failure to enforce the 24h cooldown.
    """
    global _rf_annual, _rf_fetched_at
    now = time.monotonic()
    with _rf_lock:
        if (
            _rf_annual is not None
            and _rf_fetched_at is not None
            and (now - _rf_fetched_at) < _RF_TTL_SECONDS
        ):
            return _rf_annual
        try:
            irx = yf.download("^IRX", period="5d", progress=False, auto_adjust=True)
            rate = float(irx["Close"].dropna().iloc[-1]) / 100   # convert % → decimal
            _rf_annual = rate
        except Exception:
            if _rf_annual is None:
                _rf_annual = 0.0425   # fallback only when no prior cached value exists
        _rf_fetched_at = now   # always stamp — prevents retry storm on failure
        return _rf_annual


# ---------------------------------------------------------------------------
# SPY series cache (beta/correlation reference) — 24-hour TTL, thread-safe
# ---------------------------------------------------------------------------

_spy_closes: pd.Series | None = None
_spy_fetched_at: float | None = None
_spy_lock = threading.Lock()


def _get_spy_closes() -> pd.Series | None:
    """
    Return a 2-year daily SPY close series, cached for 24 hours.
    Returns None only when no prior cache exists and the fetch fails.
    """
    global _spy_closes, _spy_fetched_at
    now = time.monotonic()
    with _spy_lock:
        if (
            _spy_closes is not None
            and _spy_fetched_at is not None
            and (now - _spy_fetched_at) < _RF_TTL_SECONDS
        ):
            return _spy_closes
        try:
            spy = yf.download("SPY", period="2y", progress=False, auto_adjust=True)
            if not spy.empty:
                _spy_closes = spy["Close"].squeeze()
        except Exception:
            pass   # keep prior cache on failure
        _spy_fetched_at = now   # always stamp — prevents retry storm on failure
        return _spy_closes


def compute_signals(ticker: str, period: str = "2y") -> dict:
    """Return risk + momentum signals for *ticker*. Returns {} on failure."""
    if ticker.upper() in _SKIP:
        return {}

    try:
        raw = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    except Exception:
        return {}

    if raw.empty:
        return {}

    spy_c = _get_spy_closes()
    if spy_c is None:
        return {}

    closes = raw["Close"].squeeze()

    # Align ticker & SPY on shared dates
    df       = pd.DataFrame({"t": closes, "s": spy_c}).dropna()
    rets     = df["t"].pct_change().dropna()
    spy_rets = df["s"].pct_change().dropna()

    # Last 252 trading days (~1 year)
    r1y = rets.tail(252)
    s1y = spy_rets.tail(252)

    # ── Risk Attribution ──────────────────────────────────────────────────
    cov_mat = np.cov(r1y, s1y)
    beta    = float(cov_mat[0, 1] / cov_mat[1, 1]) if cov_mat[1, 1] != 0 else None

    ann_vol = float(r1y.std() * np.sqrt(252) * 100)   # as a percentage

    rf_annual = _get_rf_annual()
    rf_daily  = rf_annual / 252
    excess    = r1y - rf_daily
    sharpe    = float((excess.mean() / excess.std()) * np.sqrt(252)) if excess.std() != 0 else None

    corr = float(r1y.corr(s1y))

    # ── Momentum Signals (direct from ticker.info) ───────────────────────
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        info = {}

    def _clean(v, d=2):
        if v is None:
            return None
        try:
            if np.isnan(v) or np.isinf(v):
                return None
        except TypeError:
            pass
        return round(float(v), d)

    def _pct(key):
        v = info.get(key)
        if v is None:
            return None
        try:
            return round(float(v) * 100, 1)
        except (TypeError, ValueError):
            return None

    vs_ma50       = _pct("fiftyDayAverageChangePercent")
    vs_ma200      = _pct("twoHundredDayAverageChangePercent")
    from_52w_high = _pct("fiftyTwoWeekHighChangePercent")
    analyst_rec   = _clean(info.get("recommendationMean"), 1)
    try:
        _n = info.get("numberOfAnalystOpinions")
        num_analysts = int(_n) if _n is not None else None
    except (TypeError, ValueError):
        num_analysts = None

    return {
        "beta":           _clean(beta),
        "vol":            _clean(ann_vol, 1),
        "sharpe":         _clean(sharpe),
        "sharpe_rf":      round(rf_annual * 100, 2),
        "corr_spy":       _clean(corr),
        "vs_ma50":        vs_ma50,
        "vs_ma200":       vs_ma200,
        "from_52w_high":  from_52w_high,
        "analyst_rec":    analyst_rec,
        "num_analysts":   num_analysts,
    }
