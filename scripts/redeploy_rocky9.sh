#!/usr/bin/env bash
set -euo pipefail

# Rocky Linux 9 / RHEL 9 / AlmaLinux 9 용 재배포 스크립트
#
# 사용법:
#   sudo REPO_URL=<git_repository_url> \
#        BRANCH=main \
#        WEB_SECRET=<긴_시크릿> \
#        ACCESS_KEY=<접속키> \
#        bash scripts/redeploy_rocky9.sh
#
# 옵션 환경변수:
#   APP_DIR           기본 /opt/stock_check
#   RUN_USER          기본: SUDO_USER → rocky → cloud-user → root 순으로 자동
#   SERVICE_NAME      기본 stock-check-admin
#   WEB_PORT          기본 8080
#   HYUNDAI_LOGIN_ID  지정 시 .env 에 자동 기록
#   HYUNDAI_LOGIN_PW  지정 시 .env 에 자동 기록
#
# 처리 내용:
#   - dnf 로 git/python3/wget/unzip 등 설치
#   - 기존 $APP_DIR 백업 후 신규 clone + venv + 의존성 설치
#   - Google Chrome RPM 설치 + 매칭되는 chromedriver 다운로드
#   - .env / access_key.txt 생성 (기존 백업본이 있으면 복원)
#   - systemd 관리자 웹 서비스 + 스케줄러 타이머 등록
#   - 한국시간 적용 (있을 경우)

REPO_URL=${REPO_URL:-}
BRANCH=${BRANCH:-main}
APP_DIR=${APP_DIR:-/opt/stock_check}
SERVICE_NAME=${SERVICE_NAME:-stock-check-admin}
WEB_PORT=${WEB_PORT:-8080}
WEB_SECRET=${WEB_SECRET:-change-me-secret}
ACCESS_KEY=${ACCESS_KEY:-}
HYUNDAI_LOGIN_ID=${HYUNDAI_LOGIN_ID:-}
HYUNDAI_LOGIN_PW=${HYUNDAI_LOGIN_PW:-}

if [[ -z "$REPO_URL" ]]; then
  echo "[오류] REPO_URL 환경변수를 지정하세요."
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "[오류] root 권한으로 실행하세요. (sudo)"
  exit 1
fi

# --- RUN_USER 자동 감지 ---
if [[ -z "${RUN_USER:-}" ]]; then
  for cand in "${SUDO_USER:-}" rocky cloud-user ec2-user almalinux; do
    if [[ -n "$cand" ]] && id -u "$cand" >/dev/null 2>&1; then
      RUN_USER="$cand"
      break
    fi
  done
fi
RUN_USER=${RUN_USER:-root}
echo "[정보] RUN_USER = $RUN_USER"

# --- OS 확인 ---
if ! command -v dnf >/dev/null 2>&1; then
  echo "[오류] dnf 가 없습니다. Rocky/RHEL/AlmaLinux 9 에서 실행해 주세요."
  exit 1
fi

# --- 시스템 패키지 ---
dnf -y update
dnf -y install \
    git curl wget unzip ca-certificates gnupg2 \
    python3 python3-pip python3-virtualenv \
    tzdata fontconfig liberation-fonts \
    gcc

# 한국시간 (실패해도 진행)
timedatectl set-timezone Asia/Seoul 2>/dev/null || true

# --- 기존 디렉터리 백업 ---
BACKUP_DIR=""
if [[ -d "$APP_DIR" ]]; then
  BACKUP_DIR="${APP_DIR}_backup_$(date +%Y%m%d_%H%M%S)"
  echo "[정보] 기존 디렉터리 백업: $BACKUP_DIR"
  mv "$APP_DIR" "$BACKUP_DIR"
fi

mkdir -p "$(dirname "$APP_DIR")"
git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

# --- venv + 의존성 ---
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && python3 -m venv .venv"
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && source .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"

# --- Google Chrome (RPM) ---
if ! command -v google-chrome >/dev/null 2>&1; then
  echo "[정보] Google Chrome 설치"
  dnf -y install https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm
fi
google-chrome --version

# --- chromedriver (Chrome 메이저 버전 매칭) ---
CHROME_MAJOR=$(google-chrome --version | awk '{print $3}' | cut -d. -f1)
echo "[정보] Chrome major = $CHROME_MAJOR — 매칭 chromedriver 다운로드"
DRIVER_VERSION=$(curl -fsSL "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_${CHROME_MAJOR}")
wget -q -O /tmp/chromedriver-linux64.zip \
  "https://storage.googleapis.com/chrome-for-testing-public/${DRIVER_VERSION}/linux64/chromedriver-linux64.zip"
unzip -o /tmp/chromedriver-linux64.zip -d /tmp/
install -m 0755 /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver
chromedriver --version

# --- 디렉터리/파일 ---
mkdir -p "$APP_DIR/stock_check/shared"

# 접속키: ACCESS_KEY env > 백업 폴더 key > 기본값
if [[ -n "$ACCESS_KEY" ]]; then
  echo "$ACCESS_KEY" > "$APP_DIR/stock_check/shared/access_key.txt"
elif [[ -n "$BACKUP_DIR" && -f "$BACKUP_DIR/stock_check/shared/access_key.txt" ]]; then
  cp "$BACKUP_DIR/stock_check/shared/access_key.txt" "$APP_DIR/stock_check/shared/access_key.txt"
else
  echo "change-this-access-key" > "$APP_DIR/stock_check/shared/access_key.txt"
fi

# .env: 백업 복원 > 신규 기본 생성
if [[ -n "$BACKUP_DIR" && -f "$BACKUP_DIR/stock_check/shared/.env" ]]; then
  cp "$BACKUP_DIR/stock_check/shared/.env" "$APP_DIR/stock_check/shared/.env"
fi
if [[ ! -f "$APP_DIR/stock_check/shared/.env" ]]; then
  cat > "$APP_DIR/stock_check/shared/.env" <<EOF
STOCK_CHECK_DATA_ROOT=$APP_DIR/stock_check
STOCK_CHECK_ENV_FILE=$APP_DIR/stock_check/shared/.env
STOCK_CHECK_ACCESS_KEY_FILE=$APP_DIR/stock_check/shared/access_key.txt
STOCK_CHECK_WEB_SECRET=$WEB_SECRET
STOCK_CHECK_WEB_PORT=$WEB_PORT
CHROME_DRIVER_PATH=/usr/local/bin/chromedriver
EMAIL_ALERT_INTERVAL=86400
TELEGRAM_ALERT_INTERVAL=86400
TELEGRAM_REPEAT_COUNT=3
TELEGRAM_INTERVAL=10
STOCK_CHECK_ACK_ENABLED=false
EOF
fi

# 필수 경로 키는 현재 배포 경로 기준으로 덮어써서 경로 꼬임 방지
# (HYUNDAI_LOGIN_ID / HYUNDAI_LOGIN_PW 가 env 로 들어오면 .env 에 자동 기록)
export _STC_APP_DIR="$APP_DIR"
export _STC_WEB_PORT="$WEB_PORT"
export _STC_HID="$HYUNDAI_LOGIN_ID"
export _STC_HPW="$HYUNDAI_LOGIN_PW"

python3 - <<'PY_INNER'
import os
from pathlib import Path

app_dir = os.environ["_STC_APP_DIR"]
web_port = os.environ.get("_STC_WEB_PORT") or "8080"
hid = os.environ.get("_STC_HID") or ""
hpw = os.environ.get("_STC_HPW") or ""

env_path = Path(app_dir) / "stock_check" / "shared" / ".env"
rows = {}
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        rows[k.strip()] = v.strip()

rows["STOCK_CHECK_DATA_ROOT"] = f"{app_dir}/stock_check"
rows["STOCK_CHECK_ENV_FILE"] = f"{app_dir}/stock_check/shared/.env"
rows["STOCK_CHECK_ACCESS_KEY_FILE"] = f"{app_dir}/stock_check/shared/access_key.txt"
rows.setdefault("STOCK_CHECK_WEB_PORT", web_port)
rows.setdefault("CHROME_DRIVER_PATH", "/usr/local/bin/chromedriver")
if hid:
    rows["HYUNDAI_LOGIN_ID"] = hid
if hpw:
    rows["HYUNDAI_LOGIN_PW"] = hpw

content = "\n".join(f"{k}={rows[k]}" for k in sorted(rows.keys())) + "\n"
env_path.write_text(content, encoding="utf-8")
print(f"[정보] .env 갱신: {env_path} ({len(rows)} keys)")
PY_INNER

unset _STC_APP_DIR _STC_WEB_PORT _STC_HID _STC_HPW

chmod 600 "$APP_DIR/stock_check/shared/access_key.txt" "$APP_DIR/stock_check/shared/.env"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR/stock_check/shared"

# --- systemd 관리자 웹 ---
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
systemctl status "$SERVICE_NAME" --no-pager || true

# --- 스케줄러 타이머 ---
APP_DIR="$APP_DIR" RUN_USER="$RUN_USER" bash "$APP_DIR/scripts/install_scheduler_timer.sh"
systemctl status stock-check-scheduler.timer --no-pager || true

# --- firewalld 안내 ---
if systemctl is-active --quiet firewalld; then
  echo "[정보] firewalld 가 활성화돼 있습니다. 관리자 웹(${WEB_PORT}/tcp) 을 외부에서 접속하려면:"
  echo "       sudo firewall-cmd --add-port=${WEB_PORT}/tcp --permanent && sudo firewall-cmd --reload"
fi

# --- SELinux 안내 ---
if command -v getenforce >/dev/null 2>&1; then
  SE_STATE=$(getenforce || true)
  echo "[정보] SELinux 상태: $SE_STATE (코드에서 chrome --no-sandbox 사용 — 별도 조치 불필요)"
fi

echo ""
echo "[정보] 접속키 파일: $APP_DIR/stock_check/shared/access_key.txt"
echo "[정보] 접속키 값(마스킹): $(head -c 2 "$APP_DIR/stock_check/shared/access_key.txt")****"
echo "[정보] .env 경로: $APP_DIR/stock_check/shared/.env"
echo "[정보] HYUNDAI_LOGIN_ID 존재: $(grep -c '^HYUNDAI_LOGIN_ID=' "$APP_DIR/stock_check/shared/.env" || true) (없으면 직접 추가)"
echo "[완료] Rocky 9 재배포 완료: $APP_DIR"
