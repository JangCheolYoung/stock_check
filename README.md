# stock_check
재고 확인 서비스


## 실행 방법

```bash
python -m stock_check.run_monitor --site cultizm
python -m stock_check.run_monitor --site hyundai
```

## 환경변수
- `STOCK_CHECK_BASE_DIR`: 사이트 데이터 루트 디렉터리
- `STOCK_CHECK_ENV_FILE`: dotenv 파일 경로
- `ALERT_POLICY_MODE`: `v1` 또는 `v2`
- `V1_NOTIFY_INTERVAL_HOURS`: v1 알림 간격(시간)
- `V2_REPEAT_MINUTES`: v2 반복 최소 간격(분)
- `V2_REPEAT_MAX_COUNT`: v2 반복 최대 횟수
- `ERROR_ALERT_MIN_SECONDS`: 오류 알림 최소 간격(초)
