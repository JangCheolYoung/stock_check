#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/stock_check}
DATA_ROOT=${DATA_ROOT:-$APP_DIR/stock_check}
SERVICE_NAME=${SERVICE_NAME:-stock-check-scheduler}
RUN_USER=${RUN_USER:-}

if [[ "$EUID" -ne 0 ]]; then
  echo "[오류] root 권한으로 실행하세요. (sudo)"
  exit 1
fi

if [[ -z "$RUN_USER" ]]; then
  RUN_USER=$(stat -c '%U' "$APP_DIR" 2>/dev/null || true)
fi

if [[ -z "$RUN_USER" || "$RUN_USER" == "UNKNOWN" ]] || ! id -u "$RUN_USER" >/dev/null 2>&1; then
  echo "[경고] RUN_USER 자동 감지 실패 -> root로 대체"
  RUN_USER=root
fi

echo "[1] timer/service 상태"
systemctl status "${SERVICE_NAME}.timer" --no-pager || true
systemctl status "${SERVICE_NAME}.service" --no-pager || true

echo
echo "[2] 최근 타이머 스케줄"
systemctl list-timers --all | grep -E "${SERVICE_NAME}|NEXT|LAST" || true

echo
echo "[3] 최근 service 로그"
journalctl -u "${SERVICE_NAME}.service" -n 50 --no-pager || true

echo
echo "[4] 스케줄러 상태/로그 파일"
ls -l "$DATA_ROOT/shared/scheduler_state.json" "$DATA_ROOT/shared/scheduler_runs.jsonl" 2>/dev/null || true
tail -n 20 "$DATA_ROOT/shared/scheduler_runs.jsonl" 2>/dev/null || true

echo
echo "[5] 수동 1회 실행 테스트"
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && source .venv/bin/activate && PYTHONPATH='$APP_DIR' python -m stock_check.run_scheduler --once" || true

echo
echo "[6] AlertPolicy/StockStatus 오류 원인 추적(배포 파일 검사)"
grep -n "builtins\.AlertPolicy\|AlertPolicy() takes no arguments\|StockStatus" "$APP_DIR/stock_check/cultizm/stock_checker.py" "$APP_DIR/stock_check/hyundai/stock_checker.py" || true
