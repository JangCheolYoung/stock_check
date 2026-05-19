#!/usr/bin/env bash
# stockctl — stock_check 운영 통합 헬퍼
#
# 설치된 서버에서 자주 쓰는 명령(상태확인/수동실행/로그/알림초기화/ACK/터널/헬스)을
# 한 곳에 모아 둔 래퍼. 어디서 실행하든 PYTHONPATH/작업디렉터리를 자동으로 맞춘다.
#
# 사용:  bash scripts/stockctl.sh <명령> [인자...]
#        (편하게 쓰려면)  sudo ln -s /opt/stock_check/scripts/stockctl.sh /usr/local/bin/stockctl
#                          stockctl help
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/stock_check}
SITE_DEFAULT=${SITE_DEFAULT:-hyundai}
PY="$APP_DIR/.venv/bin/python"
ENV_FILE="$APP_DIR/stock_check/shared/.env"
HY_DIR="$APP_DIR/stock_check/hyundai"

c_run() { cd "$APP_DIR"; PYTHONPATH="$APP_DIR" "$@"; }

usage() {
  cat <<'EOF'
stockctl — stock_check 운영 헬퍼

사용법: stockctl <명령> [인자]

[상태/로그]
  status                서비스·타이머 상태 한눈에 (admin/scheduler/health/tunnel)
  logs [N]              오늘 hyundai 로그 마지막 N줄 (기본 50)
  logs-sched [N]        스케줄러 journal 마지막 N줄 (기본 50)
  env                   .env 주요 값 마스킹 출력

[실행]
  run [site]            재고 확인 즉시 1회 수동 실행 (락 자동 해제, 기본 hyundai)
  unlock [site]         락 파일만 제거
  test                  단위 테스트 (unittest)

[알림]
  reset-alerts [site]   telegram/email history + alert_state 초기화 → 다음 사이클 재발송
  ack <site> <dedup>    ACK 처리 (v2 반복 알림 중단)
  acks [site]           미ACK 알림 키 목록

[헬스]
  health [resource|daily]   헬스 모니터 수동 실행 (기본 resource)

[터널/배포]
  tunnel-url            현재 cloudflared quick URL 확인 + .env 자동 반영 + admin 재시작
  update [branch]       git pull + pip + 단위테스트 + 서비스 재시작 (기본 main)

  help                  이 도움말

예시:
  stockctl status
  stockctl run
  stockctl reset-alerts hyundai && stockctl run
  stockctl ack hyundai "hyundai|MNRROTW16020078001|ALL|IN_STOCK"
  stockctl health daily
EOF
}

cmd=${1:-help}
shift || true

case "$cmd" in
  status)
    echo "=== 서비스/타이머 ==="
    for u in stock-check-admin stock-check-scheduler.timer \
             stock-check-health-resource.timer stock-check-health-daily.timer; do
      printf "%-38s %s\n" "$u" "$(systemctl is-active "$u" 2>/dev/null || echo inactive)"
    done
    echo ""
    echo "=== cloudflared ==="
    pgrep -af cloudflared || echo "(cloudflared 미실행)"
    echo ""
    echo "=== 8080 LISTEN ==="
    ss -tlnp 2>/dev/null | grep ':8080' || echo "(LISTEN 없음 — admin 다운?)"
    echo ""
    echo "=== 최근 사이클 ==="
    journalctl -u stock-check-scheduler.service -n 40 --no-pager 2>/dev/null \
      | grep -E "워커 수|재고 확인 완료|소요시간|ERROR" | tail -5 || echo "(로그 없음)"
    ;;

  logs)
    n=${1:-50}
    tail -n "$n" "$HY_DIR/logs/log-$(date +%F).txt"
    ;;

  logs-sched)
    n=${1:-50}
    journalctl -u stock-check-scheduler.service -n "$n" --no-pager
    ;;

  env)
    grep -E '^(HYUNDAI_LOGIN_ID|HYUNDAI_MAX_WORKERS|ALERT_POLICY_MODE|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|SMTP_USER|EMAIL_RECIPIENTS|STOCK_CHECK_PUBLIC_URL|HEALTH_)' \
      "$ENV_FILE" 2>/dev/null | sed 's/=\(.\{0,4\}\).*/=\1***/' || echo "(.env 없음)"
    ;;

  run)
    site=${1:-$SITE_DEFAULT}
    rm -f "$APP_DIR/stock_check/$site/stock_checker.lock"
    echo "[stockctl] $site 수동 실행..."
    c_run env DEBUG_MODE=true "$PY" -m "stock_check.$site.stock_checker"
    ;;

  unlock)
    site=${1:-$SITE_DEFAULT}
    rm -f "$APP_DIR/stock_check/$site/stock_checker.lock"
    echo "[stockctl] $site 락 제거 완료"
    ;;

  test)
    c_run "$PY" -m unittest discover -s "$APP_DIR/tests"
    ;;

  reset-alerts)
    site=${1:-$SITE_DEFAULT}
    d="$APP_DIR/stock_check/$site"
    rm -f "$d/telegram_history.json" "$d/email_history.json" "$d/alert_state.json"
    echo "[stockctl] $site 알림 이력/상태 초기화 — 다음 사이클에 재발송됨"
    ;;

  ack)
    site=${1:?사용법: stockctl ack <site> <dedup_key>}
    key=${2:?사용법: stockctl ack <site> <dedup_key>}
    c_run "$PY" -m stock_check.ack_alert --site "$site" --dedup-key "$key"
    ;;

  acks)
    site=${1:-$SITE_DEFAULT}
    c_run "$PY" - "$site" <<'PY'
import json, sys
from pathlib import Path
site = sys.argv[1]
p = Path("/opt/stock_check/stock_check")/site/"alert_state.json"
if not p.exists():
    print("(alert_state.json 없음)"); raise SystemExit
d = json.load(open(p))
any_ = False
for k, v in d.items():
    if not v.get("acknowledged"):
        any_ = True
        print(f"NOT-ACKED  {k}")
        print(f"           last_sent_at={v.get('last_sent_at')} sent_count={v.get('sent_count')}")
if not any_:
    print("(미ACK 없음)")
PY
    ;;

  health)
    mode=${1:-resource}
    c_run "$PY" "$APP_DIR/scripts/health_monitor.py" --mode "$mode"
    ;;

  tunnel-url)
    log="/var/log/cloudflared-quick.log"
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" 2>/dev/null | tail -1 || true)
    if [[ -z "$url" ]]; then
      echo "[stockctl] cloudflared URL 을 찾지 못함 ($log 확인). named tunnel 이면 .env 를 직접 관리하세요."
      exit 1
    fi
    grep -v '^STOCK_CHECK_PUBLIC_URL=' "$ENV_FILE" > /tmp/.env.stockctl
    echo "STOCK_CHECK_PUBLIC_URL=$url" >> /tmp/.env.stockctl
    mv /tmp/.env.stockctl "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    systemctl restart stock-check-admin
    echo "[stockctl] STOCK_CHECK_PUBLIC_URL=$url 반영 + admin 재시작 완료"
    ;;

  update)
    branch=${1:-main}
    BRANCH="$branch" bash "$APP_DIR/scripts/update_deploy.sh"
    ;;

  help|-h|--help)
    usage
    ;;

  *)
    echo "[stockctl] 알 수 없는 명령: $cmd"
    echo ""
    usage
    exit 1
    ;;
esac
