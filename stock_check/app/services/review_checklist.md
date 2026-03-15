# Code Review Checklist (v1/v2)

- [ ] 공통 상태값이 사이트별 결과에 누락 없이 매핑되는가?
- [ ] `SEARCH_FAILED` 와 `OUT_OF_STOCK` 가 구분되는가?
- [ ] `/root/...` 하드코딩 경로가 제거되었는가?
- [ ] dedup key(`site|product|size|status`)가 알림 정책 전반에 일관되게 쓰이는가?
- [ ] v1(24시간 1회), v2(ACK 전 반복) 정책이 분리되어 테스트 가능한가?
- [ ] 텔레그램/이메일 발송 실패 시 예외가 전체 배치를 중단시키지 않는가?
- [ ] 상태 저장(`monitor_state.json`)이 업데이트되는가?
- [ ] 로깅 메시지가 운영자가 장애 원인을 식별하기 충분한가?

## Follow-up TODO

1. Telegram inline button ACK 처리(`/ack <monitor_id>` 외 UX)
2. 상태 저장소 SQLite 전환(동시성/쿼리/보관 정책 개선)
3. crawler selector 테스트 자동화(사이트 DOM 변경 대비)
4. 오류 알림 별도 채널 + 서킷브레이커(장애 폭주 완화)
