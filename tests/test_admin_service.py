import tempfile
import unittest
from pathlib import Path

from stock_check.app.config import AppConfig
from stock_check.app.services.admin_service import AdminService, MonitorSettings, NotifierSettings


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

    def test_access_key_with_utf8_bom(self):
        self.service.access_key_file.parent.mkdir(parents=True, exist_ok=True)
        self.service.access_key_file.write_text("\ufeffbom-key\n", encoding="utf-8")
        self.assertTrue(self.service.verify_access_key("bom-key"))

    def test_access_key_candidate_fallback(self):
        fallback = self.service.config.project_root / "shared" / "access_key.txt"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text("fallback-key\n", encoding="utf-8")
        self.assertTrue(self.service.verify_access_key("fallback-key"))

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

    def test_save_and_load_monitor_settings(self):
        settings = {
            "cultizm": MonitorSettings(
                site="cultizm",
                enabled=True,
                interval_minutes=15,
                start_time="08:00",
                end_time="22:00",
                cron_expression="*/15 8-22 * * *",
                policy="v2",
                repeat_interval_minutes=7,
                schedule_timezone="Asia/Seoul",
            ),
            "hyundai": MonitorSettings(
                site="hyundai",
                enabled=False,
                interval_minutes=30,
                start_time="10:00",
                end_time="20:00",
                cron_expression="",
                policy="v1",
                repeat_interval_minutes=10,
                schedule_timezone="Asia/Seoul",
            ),
        }
        self.service.save_monitor_settings(settings)
        loaded = self.service.load_monitor_settings()
        self.assertEqual(loaded["cultizm"].interval_minutes, 15)
        self.assertEqual(loaded["cultizm"].policy, "v2")
        self.assertEqual(loaded["cultizm"].schedule_timezone, "Asia/Seoul")
        self.assertFalse(loaded["hyundai"].enabled)

    def test_scheduler_logs_append_and_load(self):
        self.service.append_scheduler_log({"site": "cultizm", "reason": "first_run", "ran": True, "run_result": "ok"})
        self.service.append_scheduler_log({"site": "hyundai", "reason": "out_of_window", "ran": False, "run_result": "skipped"})

        logs = self.service.load_scheduler_logs(limit=2)
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["site"], "hyundai")
        self.assertEqual(logs[1]["site"], "cultizm")


if __name__ == "__main__":
    unittest.main()
