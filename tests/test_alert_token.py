"""ACK 링크 토큰 단위 테스트."""

import os
import time
import unittest


class AlertTokenTests(unittest.TestCase):
    def setUp(self):
        os.environ["STOCK_CHECK_WEB_SECRET"] = "unit-test-secret-1234567890"

    def tearDown(self):
        os.environ.pop("STOCK_CHECK_WEB_SECRET", None)

    def test_roundtrip(self):
        from stock_check.app.services.alert_token import make_ack_token, verify_ack_token

        token = make_ack_token("hyundai", "hyundai|MNRROTW16020078001|ALL|IN_STOCK", ttl_hours=1)
        result = verify_ack_token(token)
        self.assertIsNotNone(result)
        site, key = result
        self.assertEqual(site, "hyundai")
        self.assertEqual(key, "hyundai|MNRROTW16020078001|ALL|IN_STOCK")

    def test_dedup_key_with_many_pipes(self):
        from stock_check.app.services.alert_token import make_ack_token, verify_ack_token

        key = "cultizm|RRL|Lot 271|M|IN_STOCK"  # 내부 '|' 다수
        token = make_ack_token("cultizm", key)
        result = verify_ack_token(token)
        self.assertIsNotNone(result)
        site, parsed = result
        self.assertEqual(site, "cultizm")
        self.assertEqual(parsed, key)

    def test_tampered_signature_fails(self):
        from stock_check.app.services.alert_token import make_ack_token, verify_ack_token

        token = make_ack_token("hyundai", "x|y|IN_STOCK")
        # 서명 부분 한 글자 변경
        head, sig = token.split(".", 1)
        tampered = f"{head}.{('A' if sig[0] != 'A' else 'B')}{sig[1:]}"
        self.assertIsNone(verify_ack_token(tampered))

    def test_different_secret_fails(self):
        from stock_check.app.services.alert_token import make_ack_token, verify_ack_token

        token = make_ack_token("hyundai", "abc|ALL|IN_STOCK")
        os.environ["STOCK_CHECK_WEB_SECRET"] = "different-secret"
        try:
            self.assertIsNone(verify_ack_token(token))
        finally:
            os.environ["STOCK_CHECK_WEB_SECRET"] = "unit-test-secret-1234567890"

    def test_expired_token_fails(self):
        from stock_check.app.services import alert_token

        # 직접 짧은 TTL 로 생성한 뒤 시각 진행을 monkeypatch
        token = alert_token.make_ack_token("hyundai", "p|ALL|IN_STOCK", ttl_hours=1)
        orig_time = alert_token.time.time
        try:
            alert_token.time.time = lambda: orig_time() + 3 * 3600  # 3시간 후
            self.assertIsNone(alert_token.verify_ack_token(token))
        finally:
            alert_token.time.time = orig_time

    def test_malformed_token(self):
        from stock_check.app.services.alert_token import verify_ack_token

        self.assertIsNone(verify_ack_token(""))
        self.assertIsNone(verify_ack_token("nodotseparator"))
        self.assertIsNone(verify_ack_token("aa.bb"))  # base64 디코드/페이로드 깨짐

    def test_build_ack_link_requires_public_url(self):
        from stock_check.app.services.alert_token import build_ack_link

        os.environ.pop("STOCK_CHECK_PUBLIC_URL", None)
        self.assertIsNone(build_ack_link("hyundai", "p|ALL|IN_STOCK"))

        os.environ["STOCK_CHECK_PUBLIC_URL"] = "http://example.com:8080/"
        os.environ["STOCK_CHECK_ACK_ENABLED"] = "true"
        try:
            link = build_ack_link("hyundai", "p|ALL|IN_STOCK")
            self.assertIsNotNone(link)
            self.assertTrue(link.startswith("http://example.com:8080/ack?t="))
        finally:
            os.environ.pop("STOCK_CHECK_PUBLIC_URL", None)
            os.environ.pop("STOCK_CHECK_ACK_ENABLED", None)


if __name__ == "__main__":
    unittest.main()
