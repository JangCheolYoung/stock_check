import json
from datetime import datetime
from pathlib import Path


class StateRepository:
    def __init__(self, site_dir: Path):
        self.site_dir = site_dir
        self.site_dir.mkdir(parents=True, exist_ok=True)
        self.filepath = self.site_dir / "monitor_state.json"

    def load(self) -> dict:
        if self.filepath.exists():
            return json.loads(self.filepath.read_text(encoding="utf-8"))
        return {}

    def save(self, state: dict) -> None:
        self.filepath.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def upsert_result(self, dedup_key: str, status: str, message: str = "", acked: bool = False) -> None:
        state = self.load()
        row = state.get(dedup_key, {})
        now = datetime.utcnow().isoformat()
        row.update(
            {
                "last_status": status,
                "last_message": message,
                "last_checked_at": now,
                "last_error_at": now if "ERROR" in status or "FAILED" in status else row.get("last_error_at"),
                "acknowledged_at": now if acked else row.get("acknowledged_at"),
                "notification_count": row.get("notification_count", 0),
            }
        )
        state[dedup_key] = row
        self.save(state)

    def mark_notified(self, dedup_key: str) -> None:
        state = self.load()
        row = state.get(dedup_key, {})
        row["last_notified_at"] = datetime.utcnow().isoformat()
        row["notification_count"] = row.get("notification_count", 0) + 1
        state[dedup_key] = row
        self.save(state)
