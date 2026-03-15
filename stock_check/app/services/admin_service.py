import os
from dataclasses import dataclass
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

    def verify_access_key(self, candidate: str) -> bool:
        if not candidate:
            return False
        if not self.access_key_file.exists():
            return False
        expected = self.access_key_file.read_text(encoding="utf-8").strip()
        return candidate.strip() == expected and expected != ""

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
