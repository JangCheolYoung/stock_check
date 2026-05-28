#!/usr/bin/env bash
set -euo pipefail

# 사용법:
# sudo BRANCH=main bash scripts/update_deploy.sh

APP_DIR=${APP_DIR:-/opt/stock_check}
BRANCH=${BRANCH:-main}
RUN_USER=${RUN_USER:-}
SERVICE_NAME=${SERVICE_NAME:-stock-check-admin}

if [[ "$EUID" -ne 0 ]]; then
  echo "[오류] root 권한으로 실행하세요. (sudo)"
  exit 1
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "[오류] $APP_DIR 가 git 저장소가 아닙니다."
  exit 1
fi

if [[ -z "$RUN_USER" ]]; then
  RUN_USER=$(stat -c '%U' "$APP_DIR" 2>/dev/null || true)
fi

if [[ -z "$RUN_USER" || "$RUN_USER" == "UNKNOWN" ]] || ! id -u "$RUN_USER" >/dev/null 2>&1; then
  echo "[경고] RUN_USER 자동 감지 실패 -> root로 대체"
  RUN_USER=root
fi

sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && git fetch --all --prune && git checkout '$BRANCH' && git pull origin '$BRANCH'"
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && source .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && source .venv/bin/activate && python -m unittest discover -s tests -p 'test_*.py'"

systemctl restart "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager

echo "[정보] 사이트별 스케줄러 타이머 설치/갱신 적용"
systemctl disable --now stock-check-scheduler.timer >/dev/null 2>&1 || true
APP_DIR="$APP_DIR" RUN_USER="$RUN_USER" SERVICE_NAME=stock-check-cultizm-scheduler SCHEDULER_SITE=cultizm ON_CALENDAR="*-*-* *:*:00" \
  bash "$APP_DIR/scripts/install_scheduler_timer.sh"
APP_DIR="$APP_DIR" RUN_USER="$RUN_USER" SERVICE_NAME=stock-check-hyundai-scheduler SCHEDULER_SITE=hyundai ON_CALENDAR="*-*-* *:00/5:00" \
  bash "$APP_DIR/scripts/install_scheduler_timer.sh"
systemctl status stock-check-cultizm-scheduler.timer --no-pager
systemctl status stock-check-hyundai-scheduler.timer --no-pager

if ! systemctl is-active --quiet stock-check-cultizm-scheduler.timer; then
  echo "[오류] stock-check-cultizm-scheduler.timer 가 active 상태가 아닙니다."
  exit 1
fi

if ! systemctl is-active --quiet stock-check-hyundai-scheduler.timer; then
  echo "[오류] stock-check-hyundai-scheduler.timer 가 active 상태가 아닙니다."
  exit 1
fi

echo "[완료] 업데이트 배포 완료"
