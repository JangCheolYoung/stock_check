import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from stock_check.app.config import AppConfig
from stock_check.app.services.admin_service import AdminService, MonitorSettings
from stock_check.run_scheduler import SchedulerRuntime, cron_matches, in_time_window


class SchedulerLogicTests(unittest.TestCase):
    def test_time_window_normal(self):
        now = datetime(2026, 3, 15, 10, 0)
        self.assertTrue(in_time_window(now, "09:00", "23:00"))
        self.assertFalse(in_time_window(datetime(2026, 3, 15, 8, 59), "09:00", "23:00"))

    def test_time_window_cross_midnight(self):
        self.assertTrue(in_time_window(datetime(2026, 3, 15, 1, 0), "23:00", "03:00"))
        self.assertFalse(in_time_window(datetime(2026, 3, 15, 12, 0), "23:00", "03:00"))

    def test_cron_matches(self):
        self.assertTrue(cron_matches("*/10 * * * *", datetime(2026, 3, 15, 10, 20)))
        self.assertFalse(cron_matches("*/10 * * * *", datetime(2026, 3, 15, 10, 23)))


class SchedulerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        data_root = root / "stock_check"
        config = AppConfig(project_root=root, data_root=data_root, env_file=data_root / "shared" / ".env")
        self.service = AdminService(config)
        self.runtime = SchedulerRuntime(self.service)

    def tearDown(self):
        self.tmp.cleanup()

    def test_should_run_interval_first(self):
        settings = MonitorSettings(site="cultizm", enabled=True, interval_minutes=10, schedule_timezone="Asia/Seoul")
        should, reason = self.runtime.should_run("cultizm", settings, datetime(2026, 3, 15, 10, 0), {})
        self.assertTrue(should)
        self.assertEqual(reason, "first_run")

    def test_should_run_cron(self):
        settings = MonitorSettings(site="cultizm", enabled=True, cron_expression="*/5 * * * *", schedule_timezone="Asia/Seoul")
        should, _ = self.runtime.should_run("cultizm", settings, datetime(2026, 3, 15, 10, 15), {})
        self.assertTrue(should)
        should2, _ = self.runtime.should_run("cultizm", settings, datetime(2026, 3, 15, 10, 16), {})
        self.assertFalse(should2)

    def test_run_once_writes_scheduler_logs(self):
        settings = {
            "cultizm": MonitorSettings(site="cultizm", enabled=False, schedule_timezone="Asia/Seoul"),
            "hyundai": MonitorSettings(site="hyundai", enabled=False, schedule_timezone="Asia/Seoul"),
        }
        self.service.save_monitor_settings(settings)

        result = self.runtime.run_once()
        self.assertIn("sites", result)

        logs = self.service.load_scheduler_logs(limit=10)
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["site"], "hyundai")
        self.assertEqual(logs[1]["site"], "cultizm")
        self.assertEqual(logs[0]["reason"], "disabled")
        self.assertEqual(logs[0]["evaluation_timezone"], "Asia/Seoul")


if __name__ == "__main__":
    unittest.main()
