# hyundai-catalog-watch

더현대(hi.thehyundai.com) RRL 브랜드 **카탈로그 전체** 재고 감시.
컬티즘 rrl-catalog-watch 의 더현대판. 기존 13-코드 `hyundai/stock_checker.py` 방식을 대체(은퇴).

## 동작 (1 사이클)
1. **디스커버리(HTTP)**: `proxy/v1/dp/search/searchResult?searchQuery=RRL&searchType=NCP_PRODUCT`
   로 현재 구매가능 RRL 전체(~135개) 수집. 키 = `slitmCd`(더현대 고유코드). 품절은 검색에서 제외됨.
2. **사이즈+수량(selenium)**: 각 제품 `/{slitmCd}` 상세 **직접 이동** → 구매하기 → 사이즈 Drawer 파싱.
   stock_check.hyundai.stock_checker 의 검증된 함수 재사용. 워커당 드라이버 1회 로그인 재사용.
3. **state.json diff → 텔레그램**: 새 제품 / 재입고(전품절→재등장) / 사이즈 재입고. 첫 실행 baseline 무알림.

## 배포 (103 서버)
- 스크립트: `/opt/hyundai-catalog-watch/hyundai_catalog_watch.py`
- systemd: `hyundai-catalog-watch.{service,timer}` (30분 주기). 옛 `stock-check-hyundai-scheduler.timer` 는 disable.
- 로그인/텔레그램 자격증명은 `/opt/stock_check/stock_check/shared/.env` 재사용(HYUNDAI_LOGIN_*, TELEGRAM_*).

## env
HCW_WORKERS(기본2), HCW_QUERY(기본RRL), HCW_MAX_PAGES, HCW_DRY_RUN, HCW_READY_SEC, HCW_HYDRATION_SEC

## 주의
stock_checker 는 import 시 모듈 최상위에서 signal.signal 을 등록 → 반드시 **메인 스레드에서 1회 import**
(워커 스레드에서 import 하면 "signal only works in main thread" 크래시).
