# v1/v2 동시 버전 개발 가이드

## v1 범위
- 공통 상태값(`StockStatus`) 도입
- 공통 알림 dedup key 규칙 적용: `site|product_identifier|size|status`
- 24시간 1회 알림 정책 (`ALERT_POLICY_MODE=v1`)
- `/root/...` 하드코딩 제거 (`STOCK_CHECK_BASE_DIR`, `STOCK_CHECK_ENV_FILE`)

## v2 범위
- ACK 전 반복 정책 (`ALERT_POLICY_MODE=v2`)
- 최소 반복 간격: `V2_REPEAT_MINUTES` (최소 5분)
- 최대 반복 횟수: `V2_REPEAT_MAX_COUNT`
- 오류 알림 rate limit: `ERROR_ALERT_MIN_SECONDS`

## 마이그레이션 포인트
1. 경로
   - 기존: `/root/{site}`
   - 변경: `STOCK_CHECK_BASE_DIR/{site}` (미지정 시 `<repo>/stock_check/{site}`)
2. 이메일 dedup
   - 기존: `product:size`
   - 변경: `dedup_prefix|size` (prefix는 표준 dedup key)
3. 상태코드
   - 기존: `success`, `no_results`, `error`, ...
   - 변경: `StockStatus` 값으로 통일
4. 운영 상태 저장
   - 신규: `{site}/ops_state.json`

## 코드리뷰 체크리스트
- [ ] `PRODUCT_NOT_FOUND`와 `SEARCH_FAILED`가 분리되어 있는가?
- [ ] 알림 dedup key가 표준 포맷인가?
- [ ] v1/v2 정책 전환이 환경변수만으로 가능한가?
- [ ] 오류 알림 rate limit이 적용되는가?
- [ ] 경로 하드코딩(`/root`)이 제거되었는가?
- [ ] 기존 이메일/텔레그램 전송 플로우가 유지되는가?

## 후속 TODO
- Telegram bot ack command(`/ack <key>`) 실제 수신 엔드포인트 연결
- crawler별 selector를 config 파일로 분리
- json 저장소를 sqlite/postgres로 전환
