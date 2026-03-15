import tempfile
import unittest
from pathlib import Path

from stock_check.app.models import StockCheckResult, StockStatus
from stock_check.app.repositories import StateRepository
from stock_check.app.services.alert_policy import AlertPolicyV1DailyOnce, AlertPolicyV2AckRepeat


class CoreModelPolicyTests(unittest.TestCase):
    def test_legacy_status_mapping(self):
        payload = {
            "status": "search_failed",
            "product": "RRL jacket",
            "available_sizes": [],
        }
        result = StockCheckResult.from_legacy("cultizm", payload, ["M"])
        self.assertEqual(result.status, StockStatus.SEARCH_FAILED)

    def test_dedup_key_contains_size_and_status(self):
        result = StockCheckResult(
            site="hyundai",
            product="Boot",
            status=StockStatus.IN_STOCK,
            matched_sizes=["10D", "9.5D"],
        )
        self.assertEqual(
            result.dedup_keys(),
            [
                "hyundai|Boot|10D|IN_STOCK",
                "hyundai|Boot|9.5D|IN_STOCK",
            ],
        )

    def test_v1_daily_once_policy(self):
        policy = AlertPolicyV1DailyOnce(interval_hours=24)
        result = StockCheckResult(site="cultizm", product="item", status=StockStatus.IN_STOCK)
        first = policy.should_notify(result, {})
        self.assertTrue(first.should_send)

    def test_v2_repeat_min_interval_and_max_count(self):
        policy = AlertPolicyV2AckRepeat(repeat_minutes=1, min_repeat_minutes=5, max_notifications=2)
        self.assertEqual(policy.repeat_minutes, 5)

        result = StockCheckResult(site="cultizm", product="item", status=StockStatus.IN_STOCK)
        blocked = policy.should_notify(result, {"notification_count": 2})
        self.assertFalse(blocked.should_send)
        self.assertEqual(blocked.reason, "max_notifications_reached")


class StateRepositoryTests(unittest.TestCase):
    def test_upsert_and_mark_notified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = StateRepository(Path(tmpdir))
            key = "cultizm|item|M|IN_STOCK"
            repo.upsert_result(key, "IN_STOCK", "ok")
            repo.mark_notified(key)
            state = repo.load()
            self.assertIn(key, state)
            self.assertEqual(state[key]["notification_count"], 1)
            self.assertIn("last_notified_at", state[key])


if __name__ == "__main__":
    unittest.main()
