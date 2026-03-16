import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stock_check.app.config import AppConfig


@dataclass
class NotifierSettings:
    smtp_server: str = "smtp.naver.com"
    smtp_port: str = "587"
    smtp_user: str = ""
    smtp_password: str = ""
    email_recipients: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class MonitorSettings:
    site: str
    enabled: bool = True
    interval_minutes: int = 10
    start_time: str = "09:00"
    end_time: str = "23:00"
    cron_expression: str = ""
    policy: str = "v1"
    repeat_interval_minutes: int = 10
    schedule_timezone: str = "Asia/Seoul"


class AdminService:
    def __init__(self, config: AppConfig | None = None):
        self.config = config or AppConfig.from_env()
        self.shared_dir = self.config.data_root / "shared"
        self.shared_dir.mkdir(parents=True, exist_ok=True)

    @property
    def env_file(self) -> Path:
        return self.config.env_file

    @property
    def access_key_file(self) -> Path:
        key_file = os.getenv("STOCK_CHECK_ACCESS_KEY_FILE")
        if key_file:
            return Path(key_file)
        return self.shared_dir / "access_key.txt"

    def access_key_candidates(self) -> list[Path]:
        """배포 경로 꼬임 상황까지 고려한 접속키 파일 후보 목록."""
        candidates: list[Path] = []
        seen: set[str] = set()

        explicit = os.getenv("STOCK_CHECK_ACCESS_KEY_FILE")
        if explicit:
            path = Path(explicit)
            candidates.append(path)
            seen.add(str(path.resolve()) if path.exists() else str(path))

        defaults = [
            self.shared_dir / "access_key.txt",
            self.config.project_root / "stock_check" / "shared" / "access_key.txt",
            self.config.project_root / "shared" / "access_key.txt",
            Path.cwd() / "stock_check" / "shared" / "access_key.txt",
            Path.cwd() / "shared" / "access_key.txt",
        ]

        for path in defaults:
            key = str(path.resolve()) if path.exists() else str(path)
            if key not in seen:
                candidates.append(path)
                seen.add(key)

        return candidates

    def _read_key(self, path: Path) -> str:
        # UTF-8 BOM 포함 파일까지 대응
        return path.read_text(encoding="utf-8-sig").strip()

    def verify_access_key(self, candidate: str) -> bool:
        if not candidate:
            return False

        probe = candidate.strip()
        for path in self.access_key_candidates():
            if not path.exists():
                continue
            try:
                expected = self._read_key(path)
            except Exception:
                continue
            if expected and probe == expected:
                return True

        return False

    def access_key_debug_info(self) -> dict:
        rows = []
        for path in self.access_key_candidates():
            exists = path.exists()
            val = ""
            if exists:
                try:
                    raw = self._read_key(path)
                    val = f"{raw[:2]}****" if raw else "(빈 값)"
                except Exception as exc:
                    val = f"읽기실패:{exc}"
            rows.append({"path": str(path), "exists": exists, "masked": val})
        return {"candidates": rows}

    def load_targets(self, site: str) -> list[dict[str, str]]:
        target_file = self.config.site_dir(site) / "targets.txt"
        if not target_file.exists():
            return []

        rows = []
        for line in target_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            keyword, sizes = self._split_target_line(line)
            rows.append({"keyword": keyword, "sizes": sizes})
        return rows

    def add_target(self, site: str, keyword: str, sizes: str) -> None:
        rows = self.load_targets(site)
        rows.append({"keyword": keyword.strip(), "sizes": self._normalize_sizes(sizes)})
        self._write_targets(site, rows)

    def update_target(self, site: str, idx: int, keyword: str, sizes: str) -> None:
        rows = self.load_targets(site)
        rows[idx] = {"keyword": keyword.strip(), "sizes": self._normalize_sizes(sizes)}
        self._write_targets(site, rows)

    def delete_target(self, site: str, idx: int) -> None:
        rows = self.load_targets(site)
        del rows[idx]
        self._write_targets(site, rows)

    def load_notifier_settings(self) -> NotifierSettings:
        env_map = self._load_env_map()
        return NotifierSettings(
            smtp_server=env_map.get("NAVER_SMTP_SERVER", "smtp.naver.com"),
            smtp_port=env_map.get("NAVER_SMTP_PORT", "587"),
            smtp_user=env_map.get("NAVER_SMTP_USER", ""),
            smtp_password=env_map.get("NAVER_SMTP_PASSWORD", ""),
            email_recipients=env_map.get("EMAIL_RECIPIENTS", ""),
            telegram_bot_token=env_map.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=env_map.get("TELEGRAM_CHAT_ID", ""),
        )

    def save_notifier_settings(self, settings: NotifierSettings) -> None:
        env_map = self._load_env_map()
        env_map.update(
            {
                "NAVER_SMTP_SERVER": settings.smtp_server.strip(),
                "NAVER_SMTP_PORT": settings.smtp_port.strip(),
                "NAVER_SMTP_USER": settings.smtp_user.strip(),
                "NAVER_SMTP_PASSWORD": settings.smtp_password.strip(),
                "EMAIL_RECIPIENTS": settings.email_recipients.strip(),
                "TELEGRAM_BOT_TOKEN": settings.telegram_bot_token.strip(),
                "TELEGRAM_CHAT_ID": settings.telegram_chat_id.strip(),
            }
        )
        self._write_env_map(env_map)

    @property
    def monitor_settings_file(self) -> Path:
        return self.shared_dir / "monitor_settings.json"

    @property
    def scheduler_log_file(self) -> Path:
        return self.shared_dir / "scheduler_runs.jsonl"

    def append_scheduler_log(self, payload: dict) -> None:
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "logged_at": datetime.now().astimezone().isoformat(),
            **payload,
        }
        with self.scheduler_log_file.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    def load_scheduler_logs(self, limit: int = 100) -> list[dict]:
        if not self.scheduler_log_file.exists():
            return []

        rows: list[dict] = []
        for line in self.scheduler_log_file.read_text(encoding="utf-8").splitlines():
            row = line.strip()
            if not row:
                continue
            try:
                rows.append(json.loads(row))
            except json.JSONDecodeError:
                continue

        if limit <= 0:
            return rows[::-1]
        return rows[::-1][:limit]

    def load_monitor_settings(self) -> dict[str, MonitorSettings]:
        defaults = {
            "cultizm": MonitorSettings(site="cultizm"),
            "hyundai": MonitorSettings(site="hyundai"),
        }
        if not self.monitor_settings_file.exists():
            return defaults

        try:
            raw = json.loads(self.monitor_settings_file.read_text(encoding="utf-8"))
            for site in ["cultizm", "hyundai"]:
                row = raw.get(site, {})
                defaults[site] = MonitorSettings(
                    site=site,
                    enabled=bool(row.get("enabled", True)),
                    interval_minutes=int(row.get("interval_minutes", 10)),
                    start_time=str(row.get("start_time", "09:00")),
                    end_time=str(row.get("end_time", "23:00")),
                    cron_expression=str(row.get("cron_expression", "")),
                    policy=str(row.get("policy", "v1")),
                    repeat_interval_minutes=int(row.get("repeat_interval_minutes", 10)),
                    schedule_timezone=str(row.get("schedule_timezone", "Asia/Seoul")),
                )
            return defaults
        except Exception:
            return defaults

    def save_monitor_settings(self, settings_by_site: dict[str, MonitorSettings]) -> None:
        payload = {}
        for site, settings in settings_by_site.items():
            payload[site] = {
                "enabled": settings.enabled,
                "interval_minutes": int(settings.interval_minutes),
                "start_time": settings.start_time,
                "end_time": settings.end_time,
                "cron_expression": settings.cron_expression,
                "policy": settings.policy,
                "repeat_interval_minutes": int(settings.repeat_interval_minutes),
                "schedule_timezone": settings.schedule_timezone,
            }
        self.monitor_settings_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _split_target_line(self, line: str) -> tuple[str, str]:
        if ":" not in line:
            return line.strip(), ""
        keyword, sizes = line.split(":", 1)
        return keyword.strip(), self._normalize_sizes(sizes)

    def _normalize_sizes(self, sizes: str) -> str:
        values = [x.strip() for x in sizes.split(",") if x.strip()]
        return ", ".join(values)

    def _write_targets(self, site: str, rows: list[dict[str, str]]) -> None:
        site_dir = self.config.site_dir(site)
        site_dir.mkdir(parents=True, exist_ok=True)
        target_file = site_dir / "targets.txt"
        lines = [f"{row['keyword']}: {self._normalize_sizes(row['sizes'])}" for row in rows if row["keyword"].strip()]
        target_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _load_env_map(self) -> dict[str, str]:
        env_map: dict[str, str] = {}
        if self.env_file.exists():
            for line in self.env_file.read_text(encoding="utf-8").splitlines():
                row = line.strip()
                if not row or row.startswith("#"):
                    continue
                if "=" not in row:
                    continue
                key, value = row.split("=", 1)
                env_map[key.strip()] = value.strip()
        return env_map

    def _write_env_map(self, env_map: dict[str, str]) -> None:
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        ordered_keys = sorted(env_map.keys())
        content = "\n".join(f"{key}={env_map[key]}" for key in ordered_keys)
        self.env_file.write_text(content + "\n", encoding="utf-8")
