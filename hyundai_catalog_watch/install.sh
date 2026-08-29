#!/usr/bin/env bash
# hyundai-catalog-watch 설치/재구축 스크립트 (root 실행)
# 이 폴더의 소스를 /opt/hyundai-catalog-watch 에 배치하고 systemd 타이머(10분 full)를 등록.
# 옛 13-코드 체커(stock-check-hyundai-scheduler)는 은퇴(disable).
#
# 사용:  sudo bash install.sh
# 옵션(env): APP_DIR, STOCK_CHECK_DIR, VENV_PY, ON_CALENDAR, HCW_WORKERS
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/hyundai-catalog-watch}
STOCK_CHECK_DIR=${STOCK_CHECK_DIR:-/opt/stock_check}
VENV_PY=${VENV_PY:-$STOCK_CHECK_DIR/.venv/bin/python}
ON_CALENDAR=${ON_CALENDAR:-"*-*-* *:00/10:00"}   # full 스윕 10분 주기(신제품+사이즈재입고 모두)
HCW_WORKERS=${HCW_WORKERS:-2}
SRC="$(cd "$(dirname "$0")" && pwd)"

[[ $EUID -eq 0 ]] || { echo "[오류] root 로 실행하세요 (sudo bash install.sh)"; exit 1; }
[[ -x "$VENV_PY" ]] || { echo "[오류] venv 없음: $VENV_PY — stock_check 먼저 설치(redeploy_from_scratch.sh)"; exit 1; }
ENV_FILE="$STOCK_CHECK_DIR/stock_check/shared/.env"
[[ -f "$ENV_FILE" ]] || echo "[경고] $ENV_FILE 없음 — HYUNDAI_LOGIN_ID/PW, SMTP_*/EMAIL_RECIPIENTS, TELEGRAM_CHAT_ID, (선택)HCW_TELEGRAM_BOT_TOKEN 를 채워야 동작"

echo "[1/5] 스크립트 배치 → $APP_DIR"
mkdir -p "$APP_DIR"
install -m 644 "$SRC/hyundai_catalog_watch.py" "$APP_DIR/hyundai_catalog_watch.py"
"$VENV_PY" -m py_compile "$APP_DIR/hyundai_catalog_watch.py"

echo "[2/5] systemd full 유닛(10분) 작성"
cat > /etc/systemd/system/hyundai-catalog-watch.service <<EOF
[Unit]
Description=Hyundai RRL Catalog Stock Watch (oneshot)
After=network.target

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=$STOCK_CHECK_DIR
Environment="PYTHONPATH=$STOCK_CHECK_DIR"
Environment="HCW_WORKERS=$HCW_WORKERS"
TimeoutStartSec=1200
ExecStart=$VENV_PY $APP_DIR/hyundai_catalog_watch.py
EOF
cat > /etc/systemd/system/hyundai-catalog-watch.timer <<EOF
[Unit]
Description=Run Hyundai RRL catalog full sweep every 10 minutes

[Timer]
OnCalendar=$ON_CALENDAR
Persistent=true
Unit=hyundai-catalog-watch.service

[Install]
WantedBy=timers.target
EOF

echo "[3/5] (선택) discover 경량 유닛 배치 — 기본 비활성(full 을 30분으로 낮출 때 활성화)"
install -m 644 "$SRC/hyundai-catalog-discover.service" /etc/systemd/system/ 2>/dev/null || true
install -m 644 "$SRC/hyundai-catalog-discover.timer"   /etc/systemd/system/ 2>/dev/null || true

echo "[4/5] daemon-reload + 타이머 활성화, 옛 체커/불필요 유닛 은퇴"
systemctl daemon-reload
systemctl enable --now hyundai-catalog-watch.timer
systemctl disable --now hyundai-catalog-discover.timer 2>/dev/null || true
systemctl disable --now stock-check-hyundai-scheduler.timer 2>/dev/null || true   # 옛 13-코드 체커 은퇴

echo "[5/5] 첫 실행(baseline, 무알림) 트리거"
systemctl start hyundai-catalog-watch.service || true

echo
echo "완료. 확인:"
echo "  systemctl list-timers hyundai-catalog-watch.timer"
echo "  tail -f $APP_DIR/watch.log"
