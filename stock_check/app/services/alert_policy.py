from dataclasses import dataclass
from datetime import datetime, timedelta

from stock_check.app.models import StockCheckResult, StockStatus


@dataclass
class PolicyDecision:
    should_send: bool
    reason: str


class AlertPolicyV1DailyOnce:
    """v1: 24시간 1회 알림"""

    def __init__(self, interval_hours: int = 24):
        self.interval = timedelta(hours=interval_hours)

    def should_notify(self, result: StockCheckResult, state_row: dict) -> PolicyDecision:
        if result.status != StockStatus.IN_STOCK:
            return PolicyDecision(False, "in_stock_only")

        last_notified = state_row.get("last_notified_at")
        if not last_notified:
            return PolicyDecision(True, "first_alert")

        elapsed = datetime.utcnow() - datetime.fromisoformat(last_notified)
        if elapsed >= self.interval:
            return PolicyDecision(True, "interval_elapsed")
        return PolicyDecision(False, "dedup_interval")


class AlertPolicyV2AckRepeat:
    """v2: ACK 전 반복 알림"""

    def __init__(self, repeat_minutes: int = 10, min_repeat_minutes: int = 5, max_notifications: int = 30):
        self.repeat_minutes = max(repeat_minutes, min_repeat_minutes)
        self.max_notifications = max_notifications

    def should_notify(self, result: StockCheckResult, state_row: dict) -> PolicyDecision:
        if result.status != StockStatus.IN_STOCK:
            return PolicyDecision(False, "in_stock_only")

        if state_row.get("acknowledged_at"):
            return PolicyDecision(False, "already_acknowledged")

        count = state_row.get("notification_count", 0)
        if count >= self.max_notifications:
            return PolicyDecision(False, "max_notifications_reached")

        last_notified = state_row.get("last_notified_at")
        if not last_notified:
            return PolicyDecision(True, "first_alert")

        elapsed = datetime.utcnow() - datetime.fromisoformat(last_notified)
        if elapsed >= timedelta(minutes=self.repeat_minutes):
            return PolicyDecision(True, "repeat_interval_elapsed")
        return PolicyDecision(False, "repeat_interval_not_elapsed")
