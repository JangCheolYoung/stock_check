# v1/v2 Migration Notes

## What changed

- Added shared status/result model under `stock_check/app/models`.
- Added crawler interface and site adapters under `stock_check/app/crawlers`.
- Added alert policy modules for v1/v2 under `stock_check/app/services/alert_policy.py`.
- Added monitor state repository for operation data in `monitor_state.json`.
- Replaced hardcoded `/root/...` paths with env-driven path resolution (`STOCK_CHECK_DATA_ROOT`, `STOCK_CHECK_ENV_FILE`).

## Migration points

1. **Path configuration**
   - Old: `/root/<site>/...`
   - New: `${STOCK_CHECK_DATA_ROOT:-<repo>/stock_check}/<site>/...`

2. **Status mapping**
   - Legacy result strings are converted to `StockStatus` enum via `StockCheckResult.from_legacy`.

3. **Notification dedup key**
   - New key format: `site|product|size|status`

4. **v1 / v2 policy split**
   - v1: `AlertPolicyV1DailyOnce`
   - v2: `AlertPolicyV2AckRepeat`

5. **Operational state**
   - New `monitor_state.json` stores last status, last checked/error/notified timestamps, notification count, ack timestamp.
