# UT Portfolio Dashboard

A real-time investment portfolio dashboard built for student-managed funds at the McCombs School of Business, University of Texas at Austin. Tracks live holdings, market prices, P&L, and performance across two funds — the **Endowment Fund** and the **Longhorn Fund**.

---

## Features

- **Live pricing** — Fetches real-time market prices via Yahoo Finance on every refresh
- **Multi-fund view** — Side-by-side KPI cards for each fund (AUM, daily P&L, unrealized P&L, return %)
- **Holdings table** — Sortable, searchable positions with cost basis, market value, weight, and unrealized P&L
- **Top movers** — Highlights positions with the largest absolute daily P&L
- **Historical snapshots** — Date picker to view holdings as of any custodian file date
- **Drag-and-drop uploads** — New custodian files uploaded via a one-click Windows batch script; server auto-detects and re-ingests
- **Cloud deployment** — Hosted on Azure VM (Ubuntu 24.04) behind Nginx with HTTP Basic Auth
- **Auto-refresh** — DataManager fingerprints the holdings folder and re-prices on change

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn |
| Data | pandas, yfinance, openpyxl, xlrd |
| Frontend | Vanilla JS, HTML5, CSS3 (no frameworks) |
| Server | Ubuntu 24.04 LTS, Nginx, systemd |
| Cloud | Microsoft Azure (VM) |
| Auth | HTTP Basic Auth middleware |

---

## Architecture

```
Browser
  │
  ▼
Nginx (port 80)
  │  reverse proxy
  ▼
FastAPI / Uvicorn (port 5175)
  │
  ├── /              → serves index.html (SPA)
  ├── /api/summary   → KPI cards data
  ├── /api/portfolio → full holdings table
  ├── /api/top-movers
  ├── /api/snapshot  → historical date view
  ├── /api/upload    → custodian file ingestion
  └── /api/refresh   → force re-price
  │
  ▼
DataManager (in-memory cache)
  │
  ├── ingestion.py   → parses custodian XLS/CSV files
  └── prices.py      → enriches with live Yahoo Finance data
```

---

## Project Structure

```
├── src/
│   ├── api.py           # FastAPI app — all endpoints + auth middleware
│   ├── data_manager.py  # Cache layer — folder fingerprinting, auto-refresh
│   ├── ingestion.py     # Custodian file parsers (Endowment + Longhorn formats)
│   └── prices.py        # Yahoo Finance enrichment + P&L calculations
├── index.html           # Single-page frontend (no framework)
├── deploy/
│   ├── deploy.sh        # One-command Azure VM provisioning script
│   └── upload_holdings.bat  # Windows drag-and-drop file uploader
├── .env.example         # Environment variable template
├── requirements.txt     # Python dependencies
└── start_dashboard.bat  # Local one-click startup (Windows)
```

---

## Running Locally

**Prerequisites:** Python 3.10+, pip

```bash
# Clone the repo
git clone https://github.com/YOUR-USERNAME/ut-portfolio-dashboard.git
cd ut-portfolio-dashboard

# Install dependencies
pip install -r requirements.txt

# Add custodian files to the Custodian Holdings/ folder
# (not included — contains real portfolio data)

# Start the server
python -m uvicorn src.api:app --host 127.0.0.1 --port 5175

# Or on Windows, double-click:
start_dashboard.bat
```

Dashboard opens at `http://127.0.0.1:5175`

---

## Cloud Deployment (Azure)

Provision a fresh Ubuntu 24.04 VM, copy project files, then:

```bash
# On the VM — installs Python, Nginx, systemd service, and starts everything
sudo bash deploy/deploy.sh
```

Set credentials in `/opt/dashboard/.env`, restart the service, and the dashboard is live at the VM's public IP.

To upload new custodian files from Windows:
```
drag custodian file → drop onto deploy/upload_holdings.bat
```

---

## Data Flow

1. Fund custodian delivers holdings file (XLS/CSV) weekly
2. File is uploaded to the VM via `upload_holdings.bat`
3. DataManager detects the new file on the next request
4. `ingestion.py` parses holdings into a unified schema
5. `prices.py` fetches live prices from Yahoo Finance for all tickers
6. Dashboard displays enriched holdings with live P&L

---

## Context

Built as part of the **Technology and Media in Investment Analysis (TMIA)** course at McCombs School of Business, UT Austin. Designed to replace manual spreadsheet workflows for student fund managers tracking real equity portfolios.
