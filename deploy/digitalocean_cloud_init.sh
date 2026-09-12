#!/bin/bash
# DigitalOcean "User Data" script -- paste this into the "Advanced Options
# > User Data" field when creating the Droplet. It runs automatically as
# root on first boot: installs Python, clones the app from GitHub, sets
# it up as a systemd service (auto-restarts on crash, auto-starts on
# reboot), and opens the firewall for port 8501.
set -e

REPO_URL="https://github.com/mallikarjunredy/nse-breakout-scanner.git"
APP_DIR="/opt/app"

apt-get update -y
apt-get install -y python3 python3-venv python3-pip git ufw

git clone "$REPO_URL" "$APP_DIR"
cd "$APP_DIR"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

cat > /etc/systemd/system/nse-scanner.service <<'EOF'
[Unit]
Description=NSE Breakout Scanner (Streamlit)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/app
ExecStart=/opt/app/.venv/bin/streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nse-scanner
systemctl start nse-scanner

ufw allow OpenSSH
ufw allow 8501/tcp
ufw --force enable
