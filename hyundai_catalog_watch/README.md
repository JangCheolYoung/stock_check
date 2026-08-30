# hyundai-catalog-watch

더현대(hi.thehyundai.com) **RRL 브랜드 카탈로그 전체** 재고 감시.
컬티즘 `rrl-catalog-watch` 의 더현대판. 기존 13-코드 방식(`stock_check/hyundai/stock_checker.py` 를 targets.txt 로 직접 검색)을 **대체(은퇴)** 한다.

배포 위치는 레포 밖(`/opt/hyundai-catalog-watch/`)이지만 **소스 정본은 이 폴더**다. 재구축은 `install.sh` 한 방.

---

## 무엇을 감지하나
- 🆕 **새 제품** 구매가능 (카탈로그에 새 코드 등장)
- 🟢 **재입고** (전체 품절돼 목록서 빠졌던 제품이 재등장)
- 🟢 **사이즈 재입고** (기존 제품의 특정 사이즈가 품절→구매가능)
- 🎟️ **쿠폰/프로모션 적용 가능 전환** (비카드 프로모/적용가<정가가 '미적용→적용'으로 바뀔 때; maxBnftList 기반, 카드 즉시할인은 제외)

알림은 **텔레그램 + 이메일** 동시. 첫 실행은 baseline(무알림).

## 동작 (1 사이클, 약 3.5~4분 / 135개 / 2워커)
1. **디스커버리 (순수 HTTP)** — `GET proxy/v1/dp/search/searchResult?searchQuery=RRL&searchType=NCP_PRODUCT&page=N&disPlaySize=36`
   → 현재 **구매가능** RRL 전체(~135개) 수집. 키 = `slitmCd`(더현대 고유 상품코드).
   더현대는 **품절 상품을 검색에서 제외**하므로, 목록에 있으면 = 최소 1사이즈 구매가능.
2. **사이즈+수량 (selenium)** — 각 제품 상세 URL `/{slitmCd}` **직접 이동** → "구매하기" → 사이즈 Drawer HTML 파싱.
   `stock_check.hyundai.stock_checker` 의 검증된 함수 재사용. **워커당 드라이버 1회 로그인 후 재사용**.
   ※ 스타일코드로 재검색하면 일부 코드가 검색 0을 반환해 실패 → **slitmCd 직접이동**이 100% 견고+빠름.
3. **diff → 알림** — `state.json`(이전 스냅샷)과 비교. 위 3종 이벤트 발생 시 텔레그램+이메일.

## 모드 (env `HCW_MODE`)
- `full`(기본) — 디스커버리 + **전체 사이즈 스윕** + 3종 이벤트. 현재 이걸 **10분 주기**로 돌려 모든 알림을 10분내 감지.
- `discover` — HTTP 디스커버리로 **새제품/재입고(제품단위)만** 빠르게(신규 slitmCd 만 사이즈 판독; 신규 0이면 ~1초).
  full 을 30분처럼 뜸하게 돌릴 때 이걸 10분 보조로 쓰면 부하↓. (현재는 full 10분이라 discover 타이머 disable.)
- 크로스프로세스 락 `/tmp/hyundai-catalog-watch.lock`(mkdir, 20분 stale) — full/discover 동시실행 방지.

---

## 사전 요구사항
- `/opt/stock_check` 에 stock_check 앱 + venv(`/opt/stock_check/.venv`) 설치돼 있어야 함
  (이 폴더 스크립트가 `stock_check.hyundai.stock_checker` 를 import). 없으면 먼저 `scripts/redeploy_from_scratch.sh`.
- Chrome + chromedriver (stock_check hyundai 체커와 동일 요구).
- `/opt/stock_check/stock_check/shared/.env` 에 아래 키:
  | 키 | 용도 |
  |---|---|
  | `HYUNDAI_LOGIN_ID` / `HYUNDAI_LOGIN_PW` | 더현대 로그인(사이즈 Drawer 는 로그인 필요) |
  | `SMTP_SERVER`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`EMAIL_RECIPIENTS` | 이메일 알림 |
  | `TELEGRAM_CHAT_ID` | 수신 chat id(유저 계정) |
  | `HCW_TELEGRAM_BOT_TOKEN` | **더현대 전용 봇** 토큰. 없으면 `TELEGRAM_BOT_TOKEN`(공용) 폴백 |

## 설치 / 재구축
```bash
sudo bash install.sh
```
하는 일: 스크립트를 `/opt/hyundai-catalog-watch/` 에 배치 → systemd `hyundai-catalog-watch.{service,timer}`(10분) 등록·활성화
→ 옛 `stock-check-hyundai-scheduler.timer` 및 `hyundai-catalog-discover.timer` 은퇴 → 첫 baseline 실행.

수동 실행/디버그:
```bash
# 1회 실행(현재 .env 로)
sudo -E /opt/stock_check/.venv/bin/python /opt/hyundai-catalog-watch/hyundai_catalog_watch.py
# 발송 없이 테스트
sudo HCW_DRY_RUN=1 HCW_MAX_PAGES=1 /opt/stock_check/.venv/bin/python /opt/hyundai-catalog-watch/hyundai_catalog_watch.py
```

## 검증
```bash
systemctl list-timers hyundai-catalog-watch.timer      # NEXT 확인
tail -n 20 /opt/hyundai-catalog-watch/watch.log        # "사이즈 스윕: N개 중 M개 판독 성공" / "완료"
```
정상 기준: 디스커버리 ~135개, 판독 성공률 ~99%+, 사이클 3.5~4분, 이벤트 0(재입고 없을 때).

## env 옵션
`HCW_WORKERS`(기본2), `HCW_QUERY`(기본RRL), `HCW_MAX_PAGES`(기본10), `HCW_DRY_RUN`,
`HCW_READY_SEC`(기본12), `HCW_HYDRATION_SEC`(기본1.5), `HCW_EMAIL_ENABLE`(기본1), `HCW_MODE`(full|discover),
`HCW_LOCK`(락 경로), `HCW_TELEGRAM_BOT_TOKEN`/`HCW_TELEGRAM_CHAT_ID`.

## 상태/로그 파일 (런타임, git 제외)
- `/opt/hyundai-catalog-watch/state.json` — 제품·사이즈 스냅샷 + `ever_seen`(신규/재입고 구분). **삭제 시 다음 실행이 baseline(무알림)로 리셋**.
- `/opt/hyundai-catalog-watch/watch.log` — 실행 로그.

## 트러블슈팅
- **`signal only works in main thread` 크래시**: `stock_checker` 는 import 시 모듈 최상위에서 `signal.signal` 을 등록한다.
  반드시 **메인 스레드에서 1회 import**(이 스크립트는 top-level 에서 import) 후 워커 스레드는 전역 `sc` 사용. 워커 안에서 import 금지.
- **판독 실패(read_ok=False)가 많다**: 로그인 실패(.env HYUNDAI_LOGIN_*), 또는 더현대 DOM 변경. 대조군으로 확인:
  `search "RRL"` 이 결과를 주는지 + 상품 상세에서 "구매하기"→Drawer 가 뜨는지.
- **사이즈 API 로 경량화?**: 사이즈는 구매하기 시 렌더되는 Drawer HTML 에서만 나온다(전용 HTTP API 없음) → **selenium 필수**.
- **디스크(실패 덤프)**: 판독 실패 시 `stock_check/hyundai/logs/failures/` 에 덤프. `stock-check-failures-reaper`(30일)가 정리.
  노이즈 완전 제거하려면 `.env` 에 `HYUNDAI_DEBUG_DUMP=0`.

## 텔레그램 봇 매핑 (2026-08-29 소스별 분리)
| 감시 | 위치 | 봇 토큰 출처(키) |
|---|---|---|
| 컬티즘 | 103 | `shared/.env` `TELEGRAM_BOT_TOKEN` |
| **더현대** | 103 | `shared/.env` `HCW_TELEGRAM_BOT_TOKEN` (없으면 위 폴백) |
| 리바이스 | Mac mini | `hd-rrl/config/local.json` `levi_telegram_bot_token` |
| 네이버 | Mac mini | `hd-rrl/config/local.json` `naver_telegram_bot_token` |
| Claude 브리지/근태/무신사 | — | 공용 `telegram_bot_token`(rrl_stock_bot) |
`chat_id` 는 유저 계정 고유값이라 전 봇 공통. 새 봇은 반드시 유저가 `/start` 해야 발송 가능.
※ 리바이스/네이버는 Mac mini 프로젝트라 이 레포에 없음(별도 백업 필요).
