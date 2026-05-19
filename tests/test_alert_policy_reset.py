"""shared.alert_policy.AlertPolicy 의 clear()(품절 → 재입고 재알림) 테스트."""

import os
import tempfile
import unittest
from pathlib import Path


class AlertPolicyClearTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["STOCK_CHECK_BASE_DIR"] = self._tmp.name
        # 사이트 디렉터리 보장
        Path(self._tmp.name, "hyundai").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.pop("STOCK_CHECK_BASE_DIR", None)
        self._tmp.cleanup()

    def _policy(self):
        from stock_check.shared.alert_policy import AlertPolicy
        return AlertPolicy("hyundai")

    def test_ack_blocks_then_clear_reenables_v2(self):
        p = self._policy()
        key = p.make_dedup_key("RRL 래더 재킷", "ALL", "IN_STOCK")

        # 최초 발송 가능
        self.assertTrue(p.should_send(key, policy_mode="v2").should_send)
        p.mark_sent(key, "IN_STOCK")

        # ACK → 이후 발송 차단
        self.assertTrue(p.ack(key))
        d = p.should_send(key, policy_mode="v2")
        self.assertFalse(d.should_send)
        self.assertEqual(d.reason, "acknowledged")

        # 품절 감지 시뮬레이션: clear → 상태 제거
        self.assertTrue(p.clear(key))

        # 재입고 → 새 이벤트로 다시 발송 가능
        self.assertTrue(p.should_send(key, policy_mode="v2").should_send)

    def test_clear_resets_v1_dedup(self):
        p = self._policy()
        key = p.make_dedup_key("상품B", "ALL", "IN_STOCK")

        self.assertTrue(p.should_send(key, policy_mode="v1").should_send)
        p.mark_sent(key, "IN_STOCK")
        # v1: 24h 내 재발송 차단
        self.assertFalse(p.should_send(key, policy_mode="v1").should_send)

        # 품절 → clear → 재입고 시 즉시 재발송 가능
        self.assertTrue(p.clear(key))
        self.assertTrue(p.should_send(key, policy_mode="v1").should_send)

    def test_clear_missing_key_is_noop(self):
        p = self._policy()
        self.assertFalse(p.clear("hyundai|없는상품|ALL|IN_STOCK"))


if __name__ == "__main__":
    unittest.main()
