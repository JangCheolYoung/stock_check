#!/usr/bin/env bash
set -euo pipefail

# stock-check 헬스 모니터 타이머 두 개 설치
#   stock-check-health-resource.timer  → 5분마다 (기본) CPU/메모리/디스크 임계 체크
#   stock-check-health-daily.timer     → 매일 06:00 Asia/Seoul 헬스 리포트
#
# 사용:
#   sudo APP_DIR=/opt/stock_check RUN_USER=root bash scripts/install_health_timers.sh
#
# 옵션 환경변수:
#   RESOURCE_INTERVAL  기본 5min  (systemd OnUnitActiveSec 형식: 5min / 10min / 1min)
#   DAILY_TIME         기본 06:00:00  (HH:MM:SS, Asia/Seoul 기준)

APP_DIR=${APP_DIR:-/opt/stock_check}
RUN_USER=${RUN_USER:-}
RESOURCE_INTERVAL=${RESOURCE_INTERVAL:-5min}
DAILY_TIME=${DAILY_TIME:-06:00:00}

if [[ "$EUID" -ne 0 ]]; then
  echo "[오류] root 권한으로 실행하세요. (sudo)"
  exit 1
fi

if [[ -z "$RUN_USER" ]]; then
  RUN_USER=$(stat -c '%U' "$APP_DIR" 2>/dev/null || true)
fi
if [[ -z "$RUN_USER" || "$RUN_USER" == "UNKNOWN" ]] || ! id -u "$RUN_USER" >/dev/null 2>&1; then
  echo "[경고] RUN_USER 자동 감지 실패 -> root 로 대체"
  RUN_USER=root
fi

echo "[정보] APP_DIR=$APP_DIR  RUN_USER=$RUN_USER  RESOURCE_INTERVAL=$RESOURCE_INTERVAL  DAILY_TIME=$DAILY_TIME"

# --- resource service/timer ---
cat > /etc/systemd/system/stock-check-health-resource.service <<EOF
[Unit]
Description=Stock Check — Resource Threshold Check (oneshot)
After=network.target

[Service]
Type=oneshot
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
Environment="PYTHONPATH=$APP_DIR"
Environment="STOCK_CHECK_DATA_ROOT=$APP_DIR/stock_check"
Environment="STOCK_CHECK_ENV_FILE=$APP_DIR/stock_check/shared/.env"
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/scripts/health_monitor.py --mode resource
EOF

cat > /etc/systemd/system/stock-check-health-resource.timer <<EOF
[Unit]
Description=Run stock-check resource threshold check every ${RESOURCE_INTERVAL}

[Timer]
OnBootSec=2min
OnUnitActiveSec=${RESOURCE_INTERVAL}
AccuracySec=15s
Persistent=true
Unit=stock-check-health-resource.service

[Install]
WantedBy=timers.target
EOF

# --- daily service/timer ---
cat > /etc/systemd/system/stock-check-health-daily.service <<EOF
[Unit]
Description=Stock Check — Daily Health Report (oneshot)
After=network.target

[Service]
Type=oneshot
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
Environment="PYTHONPATH=$APP_DIR"
Environment="STOCK_CHECK_DATA_ROOT=$APP_DIR/stock_check"
Environment="STOCK_CHECK_ENV_FILE=$APP_DIR/stock_check/shared/.env"
Environment="TZ=Asia/Seoul"
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/scripts/health_monitor.py --mode daily
EOF

cat > /etc/systemd/system/stock-check-health-daily.timer <<EOF
[Unit]
Description=Daily stock-check health report at ${DAILY_TIME} KST

[Timer]
OnCalendar=*-*-* ${DAILY_TIME} Asia/Seoul
Persistent=true
Unit=stock-check-health-daily.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now stock-check-health-resource.timer
systemctl enable --now stock-check-health-daily.timer
systemctl status stock-check-health-resource.timer --no-pager || true
systemctl status stock-check-health-daily.timer --no-pager || true

echo ""
echo "[정보] 한 번 직접 실행해 동작 확인:"
echo "  sudo -u $RUN_USER $APP_DIR/.venv/bin/python $APP_DIR/scripts/health_monitor.py --mode resource"
echo "  sudo -u $RUN_USER $APP_DIR/.venv/bin/python $APP_DIR/scripts/health_monitor.py --mode daily"
