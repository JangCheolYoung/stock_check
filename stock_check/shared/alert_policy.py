import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

from stock_check.shared.settings import get_site_dir


@dataclass
class AlertDecision:
    should_send: bool
    reason: str


class AlertPolicy:
    """v1: 24시간 1회 / v2: ack 전 반복 + rate limit"""

    def __init__(self, site_name: str):
        self.site_name = site_name
        self.site_dir = get_site_dir(site_name)
        self.state_file = self.site_dir / "alert_state.json"
        self.ops_file = self.site_dir / "ops_state.json"

    def _load_json(self, path: Path, default):
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            return default
        return default

    def _save_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def make_dedup_key(self, product_identifier: str, size: str, status: str) -> str:
        return f"{self.site_name}|{product_identifier}|{size}|{status}"

    def should_send(self, dedup_key: str, policy_mode: str = "v1") -> AlertDecision:
        state = self._load_json(self.state_file, {})
        now = time.time()
        entry = state.get(dedup_key, {})

        if policy_mode == "v2":
            min_interval_min = max(5, int(os.getenv("V2_REPEAT_MINUTES", "10")))
            repeat_limit = max(1, int(os.getenv("V2_REPEAT_MAX_COUNT", "30")))
            acknowledged = entry.get("acknowledged", False)
            sent_count = int(entry.get("sent_count", 0))
            last_ts = float(entry.get("last_sent_ts", 0))

            if acknowledged:
                return AlertDecision(False, "acknowledged")
            if sent_count >= repeat_limit:
                return AlertDecision(False, "repeat_limit")
            if now - last_ts < min_interval_min * 60:
                return AlertDecision(False, "repeat_interval")
            return AlertDecision(True, "v2_repeat")

        interval_hours = max(1, int(os.getenv("V1_NOTIFY_INTERVAL_HOURS", "24")))
        last_ts = float(entry.get("last_sent_ts", 0))
        if now - last_ts < interval_hours * 3600:
            return AlertDecision(False, "v1_dedup")
        return AlertDecision(True, "v1_allowed")

    def mark_sent(self, dedup_key: str, status: str):
        state = self._load_json(self.state_file, {})
        entry = state.get(dedup_key, {})
        entry["last_sent_ts"] = time.time()
        entry["last_sent_at"] = datetime.now().isoformat()
        entry["status"] = status
        entry["sent_count"] = int(entry.get("sent_count", 0)) + 1
        entry.setdefault("acknowledged", False)
        state[dedup_key] = entry
        self._save_json(self.state_file, state)

    def ack(self, dedup_key: str):
        state = self._load_json(self.state_file, {})
        entry = state.get(dedup_key)
        if not entry:
            return False
        entry["acknowledged"] = True
        entry["acknowledged_at"] = datetime.now().isoformat()
        state[dedup_key] = entry
        self._save_json(self.state_file, state)
        return True

    def clear(self, dedup_key: str) -> bool:
        """해당 dedup_key 상태를 완전히 제거.

        품절(OUT_OF_STOCK) 감지 시 호출하면 ACK/발송카운터/마지막발송시각이
        모두 초기화되어, 재입고 시 새 이벤트로 알림이 다시 발송된다.
        """
        state = self._load_json(self.state_file, {})
        if dedup_key in state:
            del state[dedup_key]
            self._save_json(self.state_file, state)
            return True
        return False

    def record_ops_status(self, monitor_id: str, payload: Dict):
        ops = self._load_json(self.ops_file, {})
        prev = ops.get(monitor_id, {})
        now = datetime.now().isoformat()
        payload = dict(payload)
        payload.setdefault("last_checked_at", now)
        if payload.get("is_error"):
            payload["last_error_at"] = now
            payload.setdefault("last_status", prev.get("last_status", ""))
        else:
            payload["last_success_at"] = now
        merged = {**prev, **payload}
        ops[monitor_id] = merged
        self._save_json(self.ops_file, ops)

    def should_send_error_alert(self, error_type: str) -> Tuple[bool, str]:
        """유형별 rate limit(기본 일 1회)."""
        state = self._load_json(self.state_file, {})
        key = f"{self.site_name}|ERROR|{error_type}"
        entry = state.get(key, {})
        now = time.time()
        min_sec = max(60, int(os.getenv("ERROR_ALERT_MIN_SECONDS", "86400")))
        last_ts = float(entry.get("last_sent_ts", 0))
        if now - last_ts < min_sec:
            return False, "error_rate_limited"
        state[key] = {
            "last_sent_ts": now,
            "last_sent_at": datetime.now().isoformat(),
            "status": "ERROR",
            "sent_count": int(entry.get("sent_count", 0)) + 1,
        }
        self._save_json(self.state_file, state)
        return True, "error_allowed"
