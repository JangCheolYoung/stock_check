# stock_check
재고 확인 서비스

## 신규 구조(v1/v2)

- 공통 상태 모델: `stock_check/app/models/status.py`
- 공통 결과 모델: `stock_check/app/models/result.py`
- crawler 인터페이스: `stock_check/app/crawlers/base.py`
- v1/v2 알림 정책: `stock_check/app/services/alert_policy.py`
- 운영 상태 저장소: `stock_check/app/repositories/state_repository.py`
- 관리자 웹: `stock_check/app/web_admin.py`

## 관리자 웹 기능

- 접속 키 인증(서버에 저장된 키와 일치해야 로그인 가능)
- 사이트별 target CRUD
  - Cultizm target 추가/수정/삭제
  - Hyundai target 추가/수정/삭제
- 이메일/텔레그램 설정 추가/수정
  - SMTP 서버/포트/계정/비밀번호
  - EMAIL_RECIPIENTS
  - TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
- 스케줄/알림 빈도 설정
  - 사이트별 체크 간격(분), 운영 시간대, cron 표현식
  - 사이트별 정책(v1/v2), v2 반복 간격
  - 전역 알림 간격(EMAIL_ALERT_INTERVAL, TELEGRAM_ALERT_INTERVAL)

## 환경변수

- 현재 운영 정책: Cultizm/Hyundai 재고 체크는 모두 단일 워커(1코어) 고정
- `STOCK_CHECK_DATA_ROOT`: 사이트 데이터 루트 경로(기본: `<repo>/stock_check`)
- `STOCK_CHECK_ENV_FILE`: dotenv 파일 경로(기본: `<repo>/stock_check/shared/.env`)
- `STOCK_CHECK_ACCESS_KEY_FILE`: 웹 접속키 파일 경로(기본: `<repo>/stock_check/shared/access_key.txt`)
- `STOCK_CHECK_WEB_SECRET`: Flask 세션 시크릿
- `STOCK_CHECK_WEB_PORT`: 웹 포트(기본 8080)
- `EMAIL_ALERT_INTERVAL`: 이메일 중복 방지 간격(초)
- `TELEGRAM_ALERT_INTERVAL`: 텔레그램 중복 방지 간격(초)

## 로컬 실행 방법

1. 패키지 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install flask python-dotenv selenium requests
```

2. 접속 키 생성

```bash
mkdir -p stock_check/shared
echo "원하는_강한_접속키" > stock_check/shared/access_key.txt
```

3. 관리자 웹 실행

```bash
python -m stock_check.app.web_admin
```

4. 브라우저에서 접속

- `http://localhost:8080`
- 접속키 입력 후 로그인
- target CRUD 및 알림 설정 수정


## 자동 배포 스크립트

서버 경로 꼬임(`/opt/stock_check/stock_check/...`)을 정리하고, `/opt` 에서 정상 clone 구조(`/opt/stock_check/...`)로 재구성하려면 아래 스크립트를 사용하세요.

### 1) 완전 재배포(초기화)

```bash
cd /opt/stock_check
sudo REPO_URL=<git_repository_url> BRANCH=main WEB_SECRET=<긴시크릿> ACCESS_KEY=<접속키> bash scripts/redeploy_from_scratch.sh
```

- 기존 `/opt/stock_check`는 타임스탬프 백업 폴더로 이동
- `/opt/stock_check` 재-clone
- venv/의존성 설치
- Chrome/Chromedriver 설치
- systemd 서비스 생성/재시작
- 기존 백업 경로의 `.env`(알림 설정: SMTP/수신메일/텔레그램 토큰·아이디) 자동 복원
- `ACCESS_KEY`를 지정하지 않으면 기존 백업 경로의 키를 복원하고, 없으면 기본 키(`change-this-access-key`)를 생성

### 2) 코드 업데이트 배포(git pull + 테스트 + 재시작)

```bash
cd /opt/stock_check
sudo BRANCH=main bash scripts/update_deploy.sh
```

### 3) 수동 실행(점검용)

```bash
cd /opt/stock_check
bash scripts/run_admin_web.sh
```

위 스크립트는 `PYTHONPATH=/opt/stock_check`를 사용하므로 모듈 경로 오류(`No module named stock_check.app`)를 피할 수 있습니다.


### 접속키 검증 실패 시 점검

- `.env`의 `STOCK_CHECK_ACCESS_KEY_FILE` 경로와 실제 파일 경로가 일치하는지 확인
- 파일 값에 BOM/개행이 섞여 있어도 현재 로직은 자동 정리 처리
- 디버그 모드:

```bash
export STOCK_CHECK_DEBUG_AUTH=true
python -m stock_check.app.web_admin
# 또는 /debug/auth 호출
```

- 디버그 정보로 실제 탐색한 접속키 후보 경로와 존재 여부를 확인 가능

## 서버 배포(systemd) 예시

### 1) 코드 배치

- 예시 경로: `/opt/stock_check`
- 저장소 clone 후 가상환경 준비

### 2) systemd 유닛 파일

`/etc/systemd/system/stock-check-admin.service`

```ini
[Unit]
Description=Stock Check Admin Web
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/stock_check
Environment="STOCK_CHECK_DATA_ROOT=/opt/stock_check/stock_check"
Environment="STOCK_CHECK_ENV_FILE=/opt/stock_check/stock_check/shared/.env"
Environment="STOCK_CHECK_ACCESS_KEY_FILE=/opt/stock_check/stock_check/shared/access_key.txt"
Environment="STOCK_CHECK_WEB_SECRET=여기에_긴_시크릿"
Environment="STOCK_CHECK_WEB_PORT=8080"
ExecStart=/opt/stock_check/.venv/bin/python -m stock_check.app.web_admin
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### 3) 서비스 적용

```bash
sudo systemctl daemon-reload
sudo systemctl enable stock-check-admin
sudo systemctl start stock-check-admin
sudo systemctl status stock-check-admin
```

### 4) 운영 권장사항

- 외부 공개 시 반드시 리버스 프록시(Nginx) + HTTPS 적용
- 접속키 파일 권한 제한(`chmod 600`)
- `.env`, `access_key.txt` 백업 정책 수립
- 보안 강화를 위해 IP 제한 또는 VPN 접근 권장

## 마이그레이션

자세한 변경 사항과 이관 포인트는 `V1_V2_MIGRATION.md` 참고.


## 스케줄 설정 실제 동작 방식

관리 페이지의 `스케줄/알림 빈도 설정`은 `monitor_settings.json`에 저장되고,
`python -m stock_check.run_scheduler --once` 실행 시 아래 순서로 실제 반영됩니다.

- 스케줄러는 **crontab을 직접 수정하지 않습니다.**
- `scripts/install_scheduler_timer.sh`가 systemd timer를 설치하며, 타이머가 **매분** `run_scheduler --once`를 호출합니다.
- 각 호출 시점에 사이트별 조건을 내부 로직으로 판정합니다.

1. `enabled=false`면 실행하지 않음
2. 현재 시각이 `start_time ~ end_time` 범위 밖이면 실행하지 않음
3. `cron_expression` 값이 있으면 cron 매칭 시에만 실행
4. cron이 비어 있으면 `interval_minutes` 기준으로 마지막 실행 시각 대비 실행 여부 판단
5. 실행 조건이 맞으면 사이트별 `stock_checker.py`를 실행하고 성공 시 `scheduler_state.json`에 `last_run_at` 저장

### 시간 기준 정책

- 스케줄 입력값(`start_time`, `end_time`, `cron_expression`)은 **항상 `Asia/Seoul`(KST, UTC+09:00)** 기준으로 해석합니다.
- 서버가 UTC이든 다른 타임존이든 스케줄 계산은 한국 시간 기준으로 동작합니다.
- 관리자 화면에는 서버 UTC 시각과 스케줄 기준(KST) 시각을 함께 표시합니다.
- cron 시간 범위는 자정 교차를 지원합니다. 예: `19-5`는 `19:00~05:59` 구간으로 해석됩니다.

참고:
- `EMAIL_ALERT_INTERVAL`, `TELEGRAM_ALERT_INTERVAL`은 알림 중복 방지 간격(초)으로 계속 사용됩니다.
- 스케줄러 타이머 설치: `sudo bash scripts/install_scheduler_timer.sh`
- 상태 확인: `sudo systemctl status stock-check-scheduler.timer --no-pager`
- 배포 후 자동 점검: `sudo bash scripts/check_scheduler_health.sh`

### 운영 점검: admin/scheduler가 같은 설정 파일을 보는지 확인

아래 명령으로 두 서비스가 동일한 `STOCK_CHECK_DATA_ROOT`를 사용하는지 먼저 확인하세요.

```bash
sudo systemctl show -p Environment stock-check-admin.service
sudo systemctl show -p Environment stock-check-scheduler.service
```

실제 설정 파일 경로가 하나로 일치하는지 점검합니다.

```bash
sudo find /opt -type f -name monitor_settings.json 2>/dev/null
ls -li /opt/stock_check/stock_check/shared/monitor_settings.json
```

현재 앱 코드가 참조하는 실경로를 직접 확인합니다.

```bash
cd /opt/stock_check
source .venv/bin/activate
PYTHONPATH=/opt/stock_check python - <<'PY'
from stock_check.app.services.admin_service import AdminService
s = AdminService()
print("data_root=", s.config.data_root)
print("monitor_settings_file=", s.monitor_settings_file)
print("scheduler_log_file=", s.scheduler_log_file)
PY
```

### 스케줄러가 안 도는 것 같을 때 빠른 점검

`update_deploy.sh`는 매 배포 시 `install_scheduler_timer.sh`를 호출해 타이머를 설치/갱신(덮어쓰기)하고 재시작합니다.

```bash
cd /opt/stock_check
sudo bash scripts/update_deploy.sh
sudo bash scripts/check_scheduler_health.sh
```
