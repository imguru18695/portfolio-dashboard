"""
api.py
------
FastAPI backend for the UT Portfolio Dashboard.

Endpoints
---------
GET  /api/portfolio          Full enriched holdings (all funds)
GET  /api/portfolio/{fund}   Holdings for a single fund
GET  /api/summary            KPI summary stats for the header cards
GET  /api/top-movers         Top N positions by absolute daily P&L
GET  /api/status             DataManager cache status & freshness
POST /api/refresh            Force a full re-ingest + re-price
GET  /                       Serves index.html (SPA entry point)
GET  /static/...             Serves static assets alongside index.html
"""

import math
import os
import re
import time
import secrets
import base64
import shutil
from pathlib import Path
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).parent.parent          # Dashboard Project/
SRC_DIR  = Path(__file__).parent                 # src/

import sys
sys.path.insert(0, str(SRC_DIR))

from data_manager import DataManager
from ingestion import load_holdings_for_date, list_available_dates, HOLDINGS_FOLDER
from prices import enrich_with_prices, calculate_period_returns
try:
    from signals import compute_signals, _get_rf_annual as _signals_get_rf
except ImportError as _e:
    import warnings
    warnings.warn(f"[API] signals module unavailable ({_e}); /api/signals endpoints will return 503")
    compute_signals = None
    _signals_get_rf = None

# ---------------------------------------------------------------------------
# Auth configuration  (set DASHBOARD_USER / DASHBOARD_PASS env vars in prod)
# ---------------------------------------------------------------------------

_AUTH_ENABLED = os.environ.get("DASHBOARD_AUTH", "false").lower() == "true"
_DASH_USER    = os.environ.get("DASHBOARD_USER", "utfunds")
_DASH_PASS    = os.environ.get("DASHBOARD_PASS", "changeme")

_ALLOWED_UPLOAD_EXT = {".csv", ".xls", ".xlsx"}

class BasicAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware-level HTTP Basic Auth.
    Only active when DASHBOARD_AUTH=true (off by default for local dev).
    Protects all routes with a single shared team password.
    """
    async def dispatch(self, request: Request, call_next):
        if not _AUTH_ENABLED:
            return await call_next(request)

        # Allow CORS preflight through
        if request.method == "OPTIONS":
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        authenticated = False

        if auth_header.startswith("Basic "):
            try:
                decoded  = base64.b64decode(auth_header[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
                ok_user  = secrets.compare_digest(username, _DASH_USER)
                ok_pass  = secrets.compare_digest(password, _DASH_PASS)
                authenticated = ok_user and ok_pass
            except Exception:
                pass

        if not authenticated:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="UT Portfolio Dashboard"'},
            )

        return await call_next(request)

# ---------------------------------------------------------------------------
# Startup — initialise DataManager once, shared across all requests
# ---------------------------------------------------------------------------

dm: DataManager | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global dm
    print("[API] Starting up — loading portfolio data...")
    dm = DataManager()
    dm.get_portfolio()           # warm the cache on startup
    print("[API] Ready.")
    yield
    print("[API] Shutting down.")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="UT Portfolio Dashboard API",
    version="1.0.0",
    lifespan=lifespan,
)

# Auth middleware (must be added before CORS)
app.add_middleware(BasicAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(val):
    """Convert NaN / Inf to None so JSON serialisation doesn't blow up."""
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val

def _row_to_dict(row: pd.Series) -> dict:
    return {k: _clean(v) for k, v in row.items()}

def _df_to_records(df: pd.DataFrame) -> list[dict]:
    return [_row_to_dict(row) for _, row in df.iterrows()]

def _get_portfolio() -> pd.DataFrame:
    """Always go through DataManager (handles cache + refresh)."""
    if dm is None:
        raise HTTPException(status_code=503, detail="DataManager not initialised")
    df = dm.get_portfolio()
    if df.empty:
        raise HTTPException(status_code=503, detail="No portfolio data available")
    # Ensure dates are serialisable strings
    if "as_of_date" in df.columns:
        df["as_of_date"] = df["as_of_date"].astype(str)
    if "price_time" in df.columns:
        df["price_time"] = df["price_time"].astype(str)
    return df

# ---------------------------------------------------------------------------
# Portfolio endpoints
# ---------------------------------------------------------------------------

@app.get("/api/portfolio")
def get_portfolio():
    """Return all holdings across all funds."""
    df = _get_portfolio()
    return JSONResponse(content={"data": _df_to_records(df), "count": len(df)})


@app.get("/api/portfolio/{fund_name}")
def get_portfolio_by_fund(fund_name: str):
    """
    Return holdings for a specific fund.
    fund_name: 'endowment' | 'longhorn'  (case-insensitive)
    """
    df = _get_portfolio()
    name_map = {
        "endowment": "Endowment Fund",
        "longhorn":  "Longhorn Fund",
    }
    mapped = name_map.get(fund_name.lower())
    if not mapped:
        raise HTTPException(status_code=404, detail=f"Unknown fund: {fund_name}")
    filtered = df[df["fund_name"] == mapped]
    if filtered.empty:
        raise HTTPException(status_code=404, detail=f"No data for fund: {fund_name}")
    return JSONResponse(content={"fund": mapped, "data": _df_to_records(filtered), "count": len(filtered)})


# ---------------------------------------------------------------------------
# Summary endpoint  (KPI cards)
# ---------------------------------------------------------------------------

@app.get("/api/summary")
def get_summary():
    """Return top-level KPI stats for the dashboard header cards."""
    df = _get_portfolio()

    total_mv       = df["market_value"].sum()
    total_cost     = df["cost_basis"].sum()
    total_unrealzd = df["unrealized_pnl"].sum() if "unrealized_pnl" in df.columns else total_mv - total_cost
    total_daily    = df["daily_pnl"].sum()       if "daily_pnl" in df.columns else 0.0
    prior_day_mv    = total_mv - total_daily
    total_daily_pct = (total_daily / prior_day_mv * 100) if prior_day_mv != 0 else 0

    funds = []
    for fund_name, grp in df.groupby("fund_name"):
        mv       = grp["market_value"].sum()
        cost     = grp["cost_basis"].sum()
        unrealzd = grp["unrealized_pnl"].sum() if "unrealized_pnl" in grp.columns else mv - cost
        daily    = grp["daily_pnl"].sum()       if "daily_pnl" in grp.columns else 0.0
        ret_pct  = ((mv - cost) / cost * 100)   if cost != 0 else 0.0
        funds.append({
            "fund_name"     : fund_name,
            "positions"     : len(grp),
            "market_value"  : _clean(round(mv, 2)),
            "cost_basis"    : _clean(round(cost, 2)),
            "unrealized_pnl": _clean(round(unrealzd, 2)),
            "daily_pnl"     : _clean(round(daily, 2)),
            "return_pct"    : _clean(round(ret_pct, 2)),
        })

    as_of = str(df["as_of_date"].iloc[0]) if "as_of_date" in df.columns else "—"

    return {
        "as_of_date"        : as_of,
        "total_positions"   : len(df),
        "total_funds"       : df["fund_name"].nunique(),
        "combined_aum"      : _clean(round(total_mv, 2)),
        "total_cost_basis"  : _clean(round(total_cost, 2)),
        "total_unrealized"  : _clean(round(total_unrealzd, 2)),
        "total_daily_pnl"   : _clean(round(total_daily, 2)),
        "total_daily_pct"   : _clean(round(total_daily_pct, 4)),
        "funds"             : funds,
    }


# ---------------------------------------------------------------------------
# Top movers endpoint
# ---------------------------------------------------------------------------

@app.get("/api/top-movers")
def get_top_movers(n: int = 8):
    """Return top N positions by absolute daily P&L."""
    df = _get_portfolio()
    if "daily_pnl" not in df.columns:
        return {"data": []}
    top = (df.reindex(df["daily_pnl"].abs().sort_values(ascending=False).index)
             .head(n))
    return {"data": _df_to_records(top)}


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status():
    """Return DataManager cache status and market hours info."""
    if dm is None:
        raise HTTPException(status_code=503, detail="DataManager not initialised")
    status = dm.get_status()
    # Make datetimes JSON-serialisable
    for k in ("holdings_loaded_at", "prices_refreshed_at"):
        if status.get(k):
            status[k] = str(status[k])
    return status


# ---------------------------------------------------------------------------
# Force refresh endpoint
# ---------------------------------------------------------------------------

@app.post("/api/refresh")
def force_refresh():
    """Trigger a full re-ingest + re-price. Returns updated summary."""
    if dm is None:
        raise HTTPException(status_code=503, detail="DataManager not initialised")
    print("[API] Force refresh triggered by client.")
    dm.force_refresh()
    return get_summary()


# ---------------------------------------------------------------------------
# File upload endpoint  (replaces OneDrive sync in cloud deployment)
# ---------------------------------------------------------------------------

@app.post("/api/upload")
async def upload_holdings(file: UploadFile = File(...)):
    """
    Upload a new custodian holdings file (CSV or XLS/XLSX).
    Saves to the Custodian Holdings folder; DataManager auto-detects on next request.

    Usage (curl):
        curl -u utfunds:PASSWORD -F "file=@myfile.csv" https://<host>/api/upload
    """
    suffix = Path(file.filename).suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_EXT:
        raise HTTPException(status_code=400, detail=f"File type '{suffix}' not allowed. Use CSV or XLS/XLSX.")

    dest = HOLDINGS_FOLDER / Path(file.filename).name   # .name strips any path components
    try:
        contents = await file.read()   # async read keeps the event loop free
        dest.write_bytes(contents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")
    finally:
        await file.close()

    print(f"[API] Uploaded holdings file: {file.filename} → {dest}")
    return {"status": "ok", "saved_as": str(dest), "filename": file.filename}


# ---------------------------------------------------------------------------
# Base64 upload endpoint  (for Power Automate / programmatic callers)
# ---------------------------------------------------------------------------

from pydantic import BaseModel

class UploadB64Request(BaseModel):
    filename: str
    content: str   # base64-encoded file content (Power Automate sends this natively)

@app.post("/api/upload-b64")
async def upload_holdings_b64(req: UploadB64Request):
    """
    Upload a holdings file as a base64-encoded JSON payload.
    Easier to call from Power Automate than multipart/form-data.

    Body: { "filename": "file.csv", "content": "<base64>" }
    """
    suffix = Path(req.filename).suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_EXT:
        raise HTTPException(status_code=400, detail=f"File type '{suffix}' not allowed.")

    try:
        # Strip all whitespace (MIME encoders add \r\n every 76 chars),
        # then normalise URL-safe alphabet before validating.
        normalized = re.sub(r"\s+", "", req.content).replace("-", "+").replace("_", "/")
        file_bytes = base64.b64decode(normalized, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 content.")

    dest = HOLDINGS_FOLDER / Path(req.filename).name   # .name strips any path components
    try:
        dest.write_bytes(file_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    print(f"[API] Uploaded (b64) holdings file: {req.filename} → {dest}")
    return {"status": "ok", "saved_as": str(dest), "filename": req.filename}


# ---------------------------------------------------------------------------
# Available dates endpoint  (for historical snapshot date pickers)
# ---------------------------------------------------------------------------

@app.get("/api/available-dates")
def get_available_dates():
    """Return all available custodian file dates per fund."""
    try:
        dates = list_available_dates(HOLDINGS_FOLDER)
        return dates
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Historical snapshot endpoint
# ---------------------------------------------------------------------------

@app.get("/api/snapshot")
def get_snapshot(fund: str, date: str):
    """
    Return holdings snapshot for a specific fund + date.
    Uses custodian file prices — no live yfinance for historical views.

    fund : 'endowment' | 'longhorn'
    date : 'YYYY-MM-DD'
    """
    name_map = {
        "endowment": "Endowment Fund",
        "longhorn":  "Longhorn Fund",
    }
    fund_name = name_map.get(fund.lower())
    if not fund_name:
        raise HTTPException(status_code=404, detail=f"Unknown fund: {fund}")

    try:
        df = load_holdings_for_date(HOLDINGS_FOLDER, fund_name, date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Compute unrealized P&L from file data
    df["unrealized_pnl"] = (df["market_value"] - df["cost_basis"]).round(2)
    df["as_of_date"]     = df["as_of_date"].astype(str)

    # Summary row
    total_mv   = df["market_value"].sum()
    total_cost = df["cost_basis"].sum()
    total_upnl = df["unrealized_pnl"].sum()
    upnl_pct   = round(total_upnl / total_cost * 100, 2) if total_cost else 0.0

    return JSONResponse(content={
        "fund"           : fund_name,
        "date"           : date,
        "total_positions": len(df),
        "total_mv"       : _clean(round(total_mv, 2)),
        "total_cost"     : _clean(round(total_cost, 2)),
        "total_upnl"     : _clean(round(total_upnl, 2)),
        "upnl_pct"       : _clean(upnl_pct),
        "data"           : _df_to_records(df),
    })


# ---------------------------------------------------------------------------
# Period returns endpoint
# ---------------------------------------------------------------------------

@app.get("/api/returns")
def get_returns():
    """
    Calculate period returns (1D, 5D, Mar'26TD, 3M, 6M) per fund.
    Returns None for a period if data is unavailable.
    """
    df = _get_portfolio()
    try:
        returns = calculate_period_returns(df)
        return returns
    except Exception as e:
        print(f"[API] Returns calculation error: {e}")
        raise HTTPException(status_code=500, detail=f"Returns calculation failed: {e}")


# ---------------------------------------------------------------------------
# Risk-free rate endpoint  (^IRX — 13-week T-bill, annualised %)
# ---------------------------------------------------------------------------

@app.get("/api/risk-free-rate")
def get_risk_free_rate():
    """
    Return the current 3-month T-bill yield (annualised %).
    Uses the same 24-hour cache as the Sharpe ratio calculation so the
    displayed rate always matches the rate used in signals.
    """
    if _signals_get_rf is not None:
        rate_decimal = _signals_get_rf()   # cached decimal, e.g. 0.0425
        return {"rate": round(rate_decimal * 100, 4), "source": "^IRX", "status": "live"}
    return {"rate": 4.25, "source": "fallback", "status": "error"}


# ---------------------------------------------------------------------------
# Technical signals endpoints  (Risk Attribution + Momentum via yfinance)
# ---------------------------------------------------------------------------

@app.get("/api/signals/{ticker}")
def get_signals(ticker: str):
    """Risk + momentum signals for a single ticker."""
    if compute_signals is None:
        raise HTTPException(status_code=503, detail="Signals module unavailable (yfinance not installed)")
    data = compute_signals(ticker.upper())
    return {"ticker": ticker.upper(), "signals": data}


_BULK_SIGNALS_MAX = 50

@app.get("/api/bulk-signals")
def get_bulk_signals(tickers: str):
    """
    Risk + momentum signals for multiple tickers.
    tickers: comma-separated list, e.g. 'AAPL,MSFT,NVDA'
    Maximum 50 tickers per request.
    """
    if compute_signals is None:
        raise HTTPException(status_code=503, detail="Signals module unavailable (yfinance not installed)")
    ticker_list = [x.strip().upper() for x in tickers.split(",") if x.strip()]
    if len(ticker_list) > _BULK_SIGNALS_MAX:
        raise HTTPException(status_code=400, detail=f"Too many tickers: max {_BULK_SIGNALS_MAX}, got {len(ticker_list)}")
    result = {}
    for t in ticker_list:
        result[t] = compute_signals(t)
        time.sleep(0.1)
    return {"signals": result}


# ---------------------------------------------------------------------------
# Benchmark data endpoint  (yfinance-backed)
# ---------------------------------------------------------------------------

@app.get("/api/benchmark/{fund}")
def get_benchmark(fund: str):
    """
    Returns benchmark data for 'endowment' or 'longhorn':
      - returns:     {1d, 5d, 1m, 3m} as % floats
      - cumulative:  12-point indexed series (1.0 = Feb 28, 2026 base)
      - rolling_vol: 12-point 21D annualised vol series (%)
      - composition: ETF breakdown (Endowment only)
    """
    fund = fund.lower()
    if fund not in ("endowment", "longhorn"):
        raise HTTPException(status_code=400, detail="fund must be 'endowment' or 'longhorn'")
    try:
        from benchmark import get_benchmark_data
        data = get_benchmark_data(fund)
        return data
    except Exception as e:
        print(f"[API] benchmark/{fund} error: {e}")
        raise HTTPException(status_code=503, detail=f"Benchmark data unavailable: {str(e)}")


# ---------------------------------------------------------------------------
# Serve the frontend SPA
# ---------------------------------------------------------------------------

@app.get("/")
def serve_index():
    # dashboard_v2.html is the active SPA; fall back to index.html if missing
    for name in ("dashboard_v2.html", "index.html"):
        f = ROOT_DIR / name
        if f.exists():
            return FileResponse(str(f), media_type="text/html")
    raise HTTPException(status_code=404, detail="Dashboard HTML not found")


@app.get("/{path:path}")
def serve_spa(path: str):
    """Catch-all: return the SPA for any non-API path so deep links work."""
    if path == "api" or path.lower().startswith("api/"):
        raise HTTPException(status_code=404, detail=f"Not found: /{path}")
    for name in ("dashboard_v2.html", "index.html"):
        f = ROOT_DIR / name
        if f.exists():
            return FileResponse(str(f), media_type="text/html")
    raise HTTPException(status_code=404, detail="Dashboard HTML not found")


# ---------------------------------------------------------------------------
# Run directly: python src/api.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="127.0.0.1",
        port=5174,
        reload=True,
        reload_dirs=[str(SRC_DIR)],
    )
