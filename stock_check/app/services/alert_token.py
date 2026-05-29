"""ACK 링크용 서명 토큰.

알림 메시지(텔레그램/이메일) 안에 들어가는 클릭형 ACK URL 의 인증을 담당한다.
ACCESS_KEY 로그인 게이트를 거치지 않고 처리하기 위해, URL 자체에 HMAC 서명을
포함시켜 위조를 막는다. 비밀키는 STOCK_CHECK_WEB_SECRET 환경변수를 재사용.

토큰 구조: <payload_b64url>.<signature_b64url>
  payload = "<site>|<dedup_key>|<exp_unix>"
  signature = HMAC-SHA256(payload, secret)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Optional


def ack_links_enabled() -> bool:
    return os.getenv("STOCK_CHECK_ACK_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _secret() -> bytes:
    return (os.getenv("STOCK_CHECK_WEB_SECRET", "stock-check-secret") or "").encode()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_ack_token(site: str, dedup_key: str, ttl_hours: int = 72) -> str:
    """ACK URL 에 넣을 토큰 생성. 기본 만료 72시간."""
    exp = int(time.time()) + max(1, int(ttl_hours)) * 3600
    payload = f"{site}|{dedup_key}|{exp}"
    sig = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{_b64encode(payload.encode('utf-8'))}.{_b64encode(sig)}"


def verify_ack_token(token: str) -> Optional[tuple[str, str]]:
    """토큰을 검증하고 (site, dedup_key) 를 반환. 위조/만료 시 None."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64decode(payload_b64).decode("utf-8")
        sig = _b64decode(sig_b64)
    except Exception:
        return None

    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None

    parts = payload.split("|")
    if len(parts) < 3:
        return None
    try:
        exp = int(parts[-1])
    except ValueError:
        return None
    if time.time() > exp:
        return None
    site = parts[0]
    # dedup_key 안에 '|' 가 포함될 수 있어 양 끝 site/exp 사이를 전부 묶어준다.
    dedup_key = "|".join(parts[1:-1])
    if not site or not dedup_key:
        return None
    return site, dedup_key


def build_ack_link(site: str, dedup_key: str) -> Optional[str]:
    """ACK 기능이 켜져 있고 STOCK_CHECK_PUBLIC_URL 가 설정돼 있을 때만 ACK URL 반환."""
    if not ack_links_enabled():
        return None
    base = (os.getenv("STOCK_CHECK_PUBLIC_URL", "") or "").rstrip("/")
    if not base:
        return None
    token = make_ack_token(site, dedup_key)
    return f"{base}/ack?t={token}"
