#!/bin/bash
# =============================================================
# UT Portfolio Dashboard — Azure VM Deployment Script
# Ubuntu 22.04 LTS  |  Run once as root after VM provisioning
# =============================================================
set -e

APP_DIR="/opt/dashboard"
SERVICE="portfolio-dashboard"
DOMAIN=""          # e.g. dashboard.yourdomain.com  (leave blank for IP-only)
EMAIL=""           # e.g. you@email.com  (for Let's Encrypt SSL cert)

echo ""
echo "=================================================="
echo "  UT Portfolio Dashboard — Deployment"
echo "=================================================="
echo ""

# ── 1. System packages ──────────────────────────────────────
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y python3.11 python3.11-venv python3-pip nginx certbot python3-certbot-nginx

# ── 2. App directory & code ─────────────────────────────────
echo "[2/7] Setting up app directory at $APP_DIR..."
mkdir -p "$APP_DIR/Custodian Holdings"
# Copy project files (run this script from the project root)
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
      --exclude='.env' --exclude='deploy' \
      ./ "$APP_DIR/"

# ── 3. Python virtual environment ───────────────────────────
echo "[3/7] Creating Python virtual environment..."
python3.11 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ── 4. Environment file ─────────────────────────────────────
echo "[4/7] Setting up environment..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ""
    echo "  ⚠️  IMPORTANT: Edit $APP_DIR/.env and set your password!"
    echo "     nano $APP_DIR/.env"
    echo ""
fi

# ── 5. Systemd service ──────────────────────────────────────
echo "[5/7] Installing systemd service..."
cat > /etc/systemd/system/$SERVICE.service << EOF
[Unit]
Description=UT Portfolio Dashboard
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/python -m uvicorn src.api:app --host 127.0.0.1 --port 5175
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE
systemctl start $SERVICE
echo "  Service started."

# ── 6. Nginx reverse proxy ──────────────────────────────────
echo "[6/7] Configuring Nginx..."
cat > /etc/nginx/sites-available/dashboard << 'EOF'
server {
    listen 80;
    server_name _;

    # Increase upload limit for custodian files
    client_max_body_size 50M;

    location / {
        proxy_pass         http://127.0.0.1:5175;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/dashboard /etc/nginx/sites-enabled/dashboard
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
echo "  Nginx configured."

# ── 7. SSL (optional — requires domain) ─────────────────────
if [ -n "$DOMAIN" ] && [ -n "$EMAIL" ]; then
    echo "[7/7] Obtaining SSL certificate for $DOMAIN..."
    certbot --nginx -d "$DOMAIN" --email "$EMAIL" --agree-tos --non-interactive --redirect
    echo "  SSL certificate installed. Dashboard available at https://$DOMAIN"
else
    echo "[7/7] Skipping SSL (no domain configured)."
    # Get the server's public IP
    PUBLIC_IP=$(curl -s ifconfig.me)
    echo ""
    echo "  Dashboard available at: http://$PUBLIC_IP"
    echo "  (Add a domain + set DOMAIN/EMAIL above to enable HTTPS)"
fi

echo ""
echo "=================================================="
echo "  Deployment complete!"
echo ""
echo "  Next steps:"
echo "  1. Edit /opt/dashboard/.env — set your password"
echo "  2. sudo systemctl restart $SERVICE"
echo "  3. Upload custodian files via:"
echo "     curl -u USER:PASS -F 'file=@yourfile.csv' http://<IP>/api/upload"
echo "=================================================="
echo ""
