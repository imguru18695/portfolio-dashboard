#!/usr/bin/env bash
# setup.sh — run once on a fresh Ubuntu 24.04 VM to install and start the dashboard.
# Usage: bash deploy/setup.sh
set -euo pipefail

REPO_DIR="/home/ubuntu/dashboard"
SERVICE_NAME="dashboard"

echo "==> Installing system packages"
sudo apt-get update -q
sudo apt-get install -y python3 python3-pip python3-venv nginx git

echo "==> Creating Python venv"
python3 -m venv "$REPO_DIR/venv"
"$REPO_DIR/venv/bin/pip" install --upgrade pip
"$REPO_DIR/venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

echo "==> Creating holdings folder"
mkdir -p "$REPO_DIR/holdings"

echo "==> Installing systemd service"
sudo cp "$REPO_DIR/deploy/dashboard.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME

echo ""
echo "==> Set your credentials before starting:"
echo "    sudo systemctl edit $SERVICE_NAME"
echo "    Add under [Service]:"
echo "      Environment=DASHBOARD_AUTH=true"
echo "      Environment=DASHBOARD_USER=utfunds"
echo "      Environment=DASHBOARD_PASS=<strong-password>"
echo ""

echo "==> Installing nginx config"
sudo cp "$REPO_DIR/deploy/nginx.conf" /etc/nginx/sites-available/dashboard
sudo ln -sf /etc/nginx/sites-available/dashboard /etc/nginx/sites-enabled/dashboard
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "==> Done. After setting credentials, run:"
echo "    sudo systemctl start $SERVICE_NAME"
echo "    sudo systemctl status $SERVICE_NAME"
