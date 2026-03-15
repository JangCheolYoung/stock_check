import os
import tempfile
import unittest
from pathlib import Path

from stock_check.app.config import AppConfig
from stock_check.app.services.admin_service import AdminService, NotifierSettings


class AdminServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        data_root = root / "stock_check"
        env_file = data_root / "shared" / ".env"
        config = AppConfig(project_root=root, data_root=data_root, env_file=env_file)
        self.service = AdminService(config)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_access_key_verification(self):
        self.service.access_key_file.parent.mkdir(parents=True, exist_ok=True)
        self.service.access_key_file.write_text("secret-key\n", encoding="utf-8")
        self.assertTrue(self.service.verify_access_key("secret-key"))
        self.assertFalse(self.service.verify_access_key("wrong"))

    def test_target_crud(self):
        self.service.add_target("cultizm", "RRL Jacket", "M,L")
        self.service.add_target("cultizm", "RRL Belt", "W32")
        rows = self.service.load_targets("cultizm")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["sizes"], "M, L")

        self.service.update_target("cultizm", 1, "RRL Belt Updated", "W34, W36")
        rows = self.service.load_targets("cultizm")
        self.assertEqual(rows[1]["keyword"], "RRL Belt Updated")

        self.service.delete_target("cultizm", 0)
        rows = self.service.load_targets("cultizm")
        self.assertEqual(len(rows), 1)

    def test_save_and_load_notifier_settings(self):
        settings = NotifierSettings(
            smtp_server="smtp.example.com",
            smtp_port="2525",
            smtp_user="user",
            smtp_password="pass",
            email_recipients="a@test.com,b@test.com",
            telegram_bot_token="token",
            telegram_chat_id="123",
        )
        self.service.save_notifier_settings(settings)
        loaded = self.service.load_notifier_settings()
        self.assertEqual(loaded.smtp_server, "smtp.example.com")
        self.assertEqual(loaded.telegram_chat_id, "123")


if __name__ == "__main__":
    unittest.main()
