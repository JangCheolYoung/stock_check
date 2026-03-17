#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/stock_check}
RUN_USER=${RUN_USER:-}
SERVICE_NAME=${SERVICE_NAME:-stock-check-scheduler}

if [[ "$EUID" -ne 0 ]]; then
  echo "[오류] root 권한으로 실행하세요."
  exit 1
fi

if [[ -z "$RUN_USER" ]]; then
  RUN_USER=$(stat -c '%U' "$APP_DIR" 2>/dev/null || true)
fi

if [[ -z "$RUN_USER" || "$RUN_USER" == "UNKNOWN" ]] || ! id -u "$RUN_USER" >/dev/null 2>&1; then
  echo "[경고] RUN_USER 자동 감지 실패 -> root로 대체"
  RUN_USER=root
fi

cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Stock Check Scheduler Runner (oneshot)
After=network.target

[Service]
Type=oneshot
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
Environment="PYTHONPATH=$APP_DIR"
Environment="STOCK_CHECK_DATA_ROOT=$APP_DIR/stock_check"
Environment="STOCK_CHECK_ENV_FILE=$APP_DIR/stock_check/shared/.env"
Environment="STOCK_CHECK_ACCESS_KEY_FILE=$APP_DIR/stock_check/shared/access_key.txt"
ExecStart=$APP_DIR/.venv/bin/python -m stock_check.run_scheduler --once
EOF

cat > /etc/systemd/system/${SERVICE_NAME}.timer <<EOF
[Unit]
Description=Run Stock Check scheduler every minute

[Timer]
OnCalendar=*-*-* *:*:00
Persistent=true
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}.timer
systemctl restart ${SERVICE_NAME}.timer
systemctl status ${SERVICE_NAME}.timer --no-pager

# 단발 실행으로 unit 설정 정상 여부 즉시 검증
systemctl start ${SERVICE_NAME}.service || true
systemctl status ${SERVICE_NAME}.service --no-pager || true

echo "[완료] ${SERVICE_NAME}.timer 설치 완료"
