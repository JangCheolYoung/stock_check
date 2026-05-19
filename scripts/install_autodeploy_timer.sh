#!/usr/bin/env bash
# 폴링 자동배포 systemd timer 설치.
#   stock-check-autodeploy.timer → 기본 2분마다 origin 새 커밋 확인 후 배포
#
# 사용: sudo APP_DIR=/opt/stock_check BRANCH=main RUN_USER=root \
#            bash scripts/install_autodeploy_timer.sh
#
# 옵션: INTERVAL (기본 2min, systemd OnUnitActiveSec 형식)
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/stock_check}
BRANCH=${BRANCH:-main}
RUN_USER=${RUN_USER:-}
INTERVAL=${INTERVAL:-2min}

if [[ "$EUID" -ne 0 ]]; then
  echo "[오류] root 권한으로 실행하세요. (sudo)"
  exit 1
fi

if [[ -z "$RUN_USER" ]]; then
  RUN_USER=$(stat -c '%U' "$APP_DIR" 2>/dev/null || true)
fi
if [[ -z "$RUN_USER" || "$RUN_USER" == "UNKNOWN" ]] || ! id -u "$RUN_USER" >/dev/null 2>&1; then
  RUN_USER=root
fi

echo "[정보] APP_DIR=$APP_DIR BRANCH=$BRANCH RUN_USER=$RUN_USER INTERVAL=$INTERVAL"

cat > /etc/systemd/system/stock-check-autodeploy.service <<EOF
[Unit]
Description=Stock Check — Poll & Auto Deploy (oneshot)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
Environment="BRANCH=$BRANCH"
ExecStart=/usr/bin/env bash $APP_DIR/scripts/autodeploy.sh
EOF

cat > /etc/systemd/system/stock-check-autodeploy.timer <<EOF
[Unit]
Description=Poll origin/$BRANCH every ${INTERVAL} and deploy on change

[Timer]
OnBootSec=1min
OnUnitActiveSec=${INTERVAL}
AccuracySec=20s
Persistent=true
Unit=stock-check-autodeploy.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now stock-check-autodeploy.timer
systemctl status stock-check-autodeploy.timer --no-pager || true

echo ""
echo "[정보] 즉시 한 번 시도: sudo systemctl start stock-check-autodeploy.service"
echo "[정보] 로그: journalctl -u stock-check-autodeploy.service -f"
