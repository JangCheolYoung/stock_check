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
