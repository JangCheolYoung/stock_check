#!/usr/bin/env bash
set -euo pipefail

# 사용법:
# sudo REPO_URL=<git_url> BRANCH=main WEB_SECRET=<secret> bash scripts/redeploy_from_scratch.sh

REPO_URL=${REPO_URL:-}
BRANCH=${BRANCH:-main}
APP_DIR=${APP_DIR:-/opt/stock_check}
RUN_USER=${RUN_USER:-ubuntu}
SERVICE_NAME=${SERVICE_NAME:-stock-check-admin}
WEB_PORT=${WEB_PORT:-8080}
WEB_SECRET=${WEB_SECRET:-change-me-secret}

if [[ -z "$REPO_URL" ]]; then
  echo "[오류] REPO_URL 환경변수를 지정하세요."
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "[오류] root 권한으로 실행하세요. (sudo)"
  exit 1
fi

apt update
apt install -y git curl wget unzip python3 python3-venv python3-pip ca-certificates gnupg

# 기존 폴더 백업
if [[ -d "$APP_DIR" ]]; then
  BACKUP_DIR="${APP_DIR}_backup_$(date +%Y%m%d_%H%M%S)"
  echo "[정보] 기존 디렉터리 백업: $BACKUP_DIR"
  mv "$APP_DIR" "$BACKUP_DIR"
fi

mkdir -p "$(dirname "$APP_DIR")"
git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && python3 -m venv .venv"
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && source .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"

# Chrome 설치
if ! command -v google-chrome >/dev/null 2>&1; then
  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/google-chrome.deb
  apt install -y /tmp/google-chrome.deb
fi

# Chromedriver 설치
CHROME_MAJOR=$(google-chrome --version | awk '{print $3}' | cut -d. -f1)
DRIVER_VERSION=$(curl -fsSL "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_${CHROME_MAJOR}")
wget -q -O /tmp/chromedriver-linux64.zip "https://storage.googleapis.com/chrome-for-testing-public/${DRIVER_VERSION}/linux64/chromedriver-linux64.zip"
unzip -o /tmp/chromedriver-linux64.zip -d /tmp/
install -m 0755 /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver

mkdir -p "$APP_DIR/stock_check/shared"
if [[ ! -f "$APP_DIR/stock_check/shared/access_key.txt" ]]; then
  echo "change-this-access-key" > "$APP_DIR/stock_check/shared/access_key.txt"
fi
if [[ ! -f "$APP_DIR/stock_check/shared/.env" ]]; then
  cat > "$APP_DIR/stock_check/shared/.env" <<EOF
STOCK_CHECK_DATA_ROOT=$APP_DIR/stock_check
STOCK_CHECK_ENV_FILE=$APP_DIR/stock_check/shared/.env
STOCK_CHECK_ACCESS_KEY_FILE=$APP_DIR/stock_check/shared/access_key.txt
STOCK_CHECK_WEB_SECRET=$WEB_SECRET
STOCK_CHECK_WEB_PORT=$WEB_PORT
CHROME_DRIVER_PATH=/usr/local/bin/chromedriver
EMAIL_ALERT_INTERVAL=3600
TELEGRAM_ALERT_INTERVAL=3600
EOF
fi

chmod 600 "$APP_DIR/stock_check/shared/access_key.txt" "$APP_DIR/stock_check/shared/.env"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR/stock_check/shared"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Stock Check Admin Web
After=network.target

[Service]
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
Environment="PYTHONPATH=$APP_DIR"
Environment="STOCK_CHECK_DATA_ROOT=$APP_DIR/stock_check"
Environment="STOCK_CHECK_ENV_FILE=$APP_DIR/stock_check/shared/.env"
Environment="STOCK_CHECK_ACCESS_KEY_FILE=$APP_DIR/stock_check/shared/access_key.txt"
Environment="STOCK_CHECK_WEB_SECRET=$WEB_SECRET"
Environment="STOCK_CHECK_WEB_PORT=$WEB_PORT"
ExecStart=$APP_DIR/.venv/bin/python -m stock_check.app.web_admin
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager

echo "[완료] 재배포 완료: $APP_DIR"
