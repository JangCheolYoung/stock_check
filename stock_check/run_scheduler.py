import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, time
from zoneinfo import ZoneInfo

from stock_check.app.services.admin_service import AdminService, MonitorSettings


def parse_hhmm(value: str) -> time:
    h, m = value.split(":", 1)
    return time(hour=int(h), minute=int(m))


def in_time_window(now: datetime, start: str, end: str) -> bool:
    start_t = parse_hhmm(start)
    end_t = parse_hhmm(end)
    cur = now.time()

    if start_t <= end_t:
        return start_t <= cur <= end_t
    return cur >= start_t or cur <= end_t


def _field_match(token: str, value: int) -> bool:
    token = token.strip()
    if token == "*":
        return True
    if token.startswith("*/"):
        step = int(token[2:])
        return value % step == 0
    if "-" in token:
        a, b = token.split("-", 1)
        return int(a) <= value <= int(b)
    return int(token) == value


def cron_matches(expr: str, now: datetime) -> bool:
    parts = expr.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    dow_val = (now.weekday() + 1) % 7

    return all(
        [
            _field_match(minute, now.minute),
            _field_match(hour, now.hour),
            _field_match(dom, now.day),
            _field_match(month, now.month),
            _field_match(dow, dow_val),
        ]
    )


class SchedulerRuntime:
    def __init__(self, service: AdminService | None = None):
        self.service = service or AdminService()
        self.state_file = self.service.shared_dir / "scheduler_state.json"

    def load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {}

    def save_state(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def _site_now(self, settings: MonitorSettings) -> datetime:
        tz_name = settings.schedule_timezone or "Asia/Seoul"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz_name = "Asia/Seoul"
            tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        return now

    def should_run(self, site: str, settings: MonitorSettings, now: datetime, state: dict) -> tuple[bool, str]:
        if not settings.enabled:
            return False, "disabled"

        if not in_time_window(now, settings.start_time, settings.end_time):
            return False, "out_of_window"

        if settings.cron_expression:
            if cron_matches(settings.cron_expression, now):
                return True, "cron_match"
            return False, "cron_not_match"

        site_state = state.get(site, {})
        last_run = site_state.get("last_run_at")
        if not last_run:
            return True, "first_run"

        last_run_at = datetime.fromisoformat(last_run)
        if last_run_at.tzinfo is None and now.tzinfo is not None:
            last_run_at = last_run_at.replace(tzinfo=now.tzinfo)

        delta = now - last_run_at
        if delta.total_seconds() >= settings.interval_minutes * 60:
            return True, "interval_elapsed"
        return False, "interval_not_elapsed"

    def run_site_checker(self, site: str) -> tuple[bool, str]:
        checker = self.service.config.site_dir(site) / "stock_checker.py"
        if not checker.exists():
            return False, f"checker_missing:{checker}"

        proc = subprocess.run(
            [sys.executable, str(checker)],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode == 0:
            return True, "ok"
        return False, f"exit_{proc.returncode}:{proc.stderr[-300:]}"

    def run_once(self) -> dict:
        executed_at = datetime.now().astimezone()
        settings = self.service.load_monitor_settings()
        state = self.load_state()

        result = {"executed_at": executed_at.isoformat(), "sites": {}}

        for site, conf in settings.items():
            now_for_site = self._site_now(conf)
            should, reason = self.should_run(site, conf, now_for_site, state)
            info = {
                "reason": reason,
                "settings": asdict(conf),
                "ran": False,
                "run_result": "skipped",
                "evaluation_now": now_for_site.isoformat(),
                "evaluation_timezone": conf.schedule_timezone,
            }
            if should:
                ok, msg = self.run_site_checker(site)
                info["ran"] = True
                info["run_result"] = msg
                if ok:
                    state.setdefault(site, {})["last_run_at"] = now_for_site.isoformat()

            self.service.append_scheduler_log(
                {
                    "executed_at": executed_at.isoformat(),
                    "site": site,
                    "reason": reason,
                    "ran": info["ran"],
                    "run_result": info["run_result"],
                    "settings": info["settings"],
                    "evaluation_now": info["evaluation_now"],
                    "evaluation_timezone": info["evaluation_timezone"],
                    "window_check_detail": f"{conf.start_time}~{conf.end_time}",
                }
            )
            result["sites"][site] = info

        self.save_state(state)
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", default=True)
    parser.parse_args()

    runtime = SchedulerRuntime()
    result = runtime.run_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
