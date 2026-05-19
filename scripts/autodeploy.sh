#!/usr/bin/env bash
# autodeploy — origin 의 대상 브랜치에 새 커밋이 있으면 update_deploy.sh 실행.
# 사내망(인바운드 차단) 환경용 pull 기반 자동 배포. systemd timer 로 주기 호출.
#
# 사용: BRANCH=main bash scripts/autodeploy.sh
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/stock_check}
BRANCH=${BRANCH:-main}

cd "$APP_DIR"

git fetch origin "$BRANCH" --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [[ "$LOCAL" == "$REMOTE" ]]; then
  echo "[autodeploy] 변경 없음 (HEAD=$LOCAL)"
  exit 0
fi

echo "[autodeploy] 새 커밋 감지: $LOCAL -> $REMOTE"
echo "[autodeploy] update_deploy.sh 실행 (브랜치=$BRANCH)"
BRANCH="$BRANCH" bash "$APP_DIR/scripts/update_deploy.sh"
echo "[autodeploy] 배포 완료: $(git rev-parse HEAD)"
