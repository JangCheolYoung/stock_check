#!/usr/bin/env bash
set -euo pipefail

# 사용법:
# sudo BRANCH=main bash scripts/update_deploy.sh

APP_DIR=${APP_DIR:-/opt/stock_check}
BRANCH=${BRANCH:-main}
RUN_USER=${RUN_USER:-ubuntu}
SERVICE_NAME=${SERVICE_NAME:-stock-check-admin}

if [[ "$EUID" -ne 0 ]]; then
  echo "[오류] root 권한으로 실행하세요. (sudo)"
  exit 1
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "[오류] $APP_DIR 가 git 저장소가 아닙니다."
  exit 1
fi

sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && git fetch --all --prune && git checkout '$BRANCH' && git pull origin '$BRANCH'"
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && source .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && source .venv/bin/activate && python -m unittest discover -s tests -p 'test_*.py'"

systemctl restart "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager

echo "[완료] 업데이트 배포 완료"
