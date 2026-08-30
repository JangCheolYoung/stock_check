#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_check/shared/email_utils.py
공통 이메일 발송 모듈
"""

import json
import os
import smtplib
from pathlib import Path
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re as _re
import html as _html

from dotenv import load_dotenv

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / "stock_check"


def _safe_load_dotenv(path=None, **kwargs):
    try:
        return load_dotenv(path, **kwargs) if path is not None else load_dotenv(**kwargs)
    except PermissionError:
        return False


_safe_load_dotenv(CURRENT_DIR / ".env")
_safe_load_dotenv()


def _site_dir(site_name):
    data_root = Path(os.getenv("STOCK_CHECK_DATA_ROOT", str(DEFAULT_DATA_ROOT)))
    return data_root / site_name

def load_email_history(site_name):
    try:
        history_file = _site_dir(site_name) / "email_history.json"
        if history_file.exists():
            with open(history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    except Exception:
        return {}


def save_email_history(site_name, history):
    try:
        history_file = _site_dir(site_name) / "email_history.json"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"메일 기록 저장 실패: {e}")


def can_send_email(site_name, dedup_key):
    history = load_email_history(site_name)
    if dedup_key not in history:
        return True

    last_sent = datetime.fromisoformat(history[dedup_key])
    time_diff = datetime.now() - last_sent
    email_interval_seconds = int(os.getenv("EMAIL_ALERT_INTERVAL", "86400"))
    return time_diff >= timedelta(seconds=email_interval_seconds)


def record_email_sent(site_name, dedup_key):
    history = load_email_history(site_name)
    history[dedup_key] = datetime.now().isoformat()

    week_ago = datetime.now() - timedelta(days=7)
    history = {k: v for k, v in history.items() if datetime.fromisoformat(v) > week_ago}
    save_email_history(site_name, history)


def send_stock_alert(site_name, product, sizes, url, dedup_prefix=None, ack_link=None):
    try:
        # SMTP_* 와 NAVER_SMTP_* 둘 다 호환
        smtp_server = os.getenv("SMTP_SERVER") or os.getenv("NAVER_SMTP_SERVER", "smtp.naver.com")
        smtp_port = int(os.getenv("SMTP_PORT") or os.getenv("NAVER_SMTP_PORT") or "587")
        smtp_user = os.getenv("SMTP_USER") or os.getenv("NAVER_SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD") or os.getenv("NAVER_SMTP_PASSWORD")
        recipients = [r.strip() for r in os.getenv("EMAIL_RECIPIENTS", "").split(",") if r.strip()]

        if not smtp_user or not smtp_password or not recipients:
            print("이메일 설정이 없습니다")
            return False

        sendable_sizes = []
        for size in sizes:
            dedup_key = f"{dedup_prefix}|{size}" if dedup_prefix else f"{product}:{size}"
            if can_send_email(site_name, dedup_key):
                sendable_sizes.append(size)

        if not sendable_sizes:
            print(f"{site_name} {product} - 최근 발송 이력으로 스킵")
            return False

        subject = f"[{site_name.upper()} 재입고 알림] {product} - {', '.join(sendable_sizes)} 재고 확인됨"
        body_lines = [
            f"=== {site_name.upper()} 재고 확인 결과 ===",
            "",
            f"상품: {product}",
            f"사용가능 사이즈: {', '.join(sendable_sizes)}",
            f"링크: {url}",
            "",
            f"확인 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        if ack_link:
            body_lines.append("")
            body_lines.append(f"▶ 이 알림 그만 받기(ACK): {ack_link}")
        body = "\n".join(body_lines)

        _hb = _re.compile(r"(https?://[^\s<>\"]+)").sub(r'<a href="\1">\1</a>', _html.escape(body)).replace("\n", "<br>\n")
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(f'<html><body style="font-family:sans-serif;font-size:14px">{_hb}</body></html>', "html", "utf-8"))
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = ", ".join(recipients)

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())

        for size in sendable_sizes:
            dedup_key = f"{dedup_prefix}|{size}" if dedup_prefix else f"{product}:{size}"
            record_email_sent(site_name, dedup_key)
        return True
    except Exception as e:
        print(f"이메일 발송 실패: {e}")
        return False


def load_system_alerts(site_name):
    try:
        alert_file = _site_dir(site_name) / "system_alerts.json"
        if alert_file.exists():
            with open(alert_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    except Exception:
        return {}


def save_system_alerts(site_name, alerts):
    try:
        alert_file = _site_dir(site_name) / "system_alerts.json"
        alert_file.parent.mkdir(parents=True, exist_ok=True)
        with open(alert_file, "w", encoding="utf-8") as f:
            json.dump(alerts, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"시스템 알림 기록 저장 실패: {e}")


def can_send_system_alert(site_name, alert_type):
    alerts = load_system_alerts(site_name)
    today = datetime.now().strftime("%Y-%m-%d")
    if alert_type not in alerts:
        return True
    return alerts[alert_type].get("last_sent", "") != today


def send_system_alert(site_name, alert_type, subject, body):
    if not can_send_system_alert(site_name, alert_type):
        print(f"{site_name} 시스템 알림 스킵 (오늘 이미 발송됨): {alert_type}")
        return False

    try:
        # 시스템/운영 알림을 브릿지봇 텔레그램으로 발송(이메일 설정과 무관). 하루 1회 dedup.
        try:
            import urllib.request as _u, urllib.parse as _up
            _tok = os.getenv("HEALTH_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
            _chat = os.getenv("HEALTH_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
            if _tok and _chat:
                _text = f"⚠️ [{site_name.upper()} 시스템] {subject}\n\n{body}"
                _data = _up.urlencode({"chat_id": _chat, "text": _text}).encode()
                _u.urlopen(_u.Request(f"https://api.telegram.org/bot{_tok}/sendMessage", data=_data), timeout=10)
                _a = load_system_alerts(site_name)
                _a[alert_type] = {"last_sent": datetime.now().strftime("%Y-%m-%d"), "message": subject, "timestamp": datetime.now().isoformat()}
                save_system_alerts(site_name, _a)
        except Exception as _e:
            print(f"{site_name} 시스템 알림 텔레그램 실패: {_e}")
        smtp_server = os.getenv("NAVER_SMTP_SERVER", "smtp.naver.com")
        smtp_port = int(os.getenv("NAVER_SMTP_PORT", "587"))
        smtp_user = os.getenv("NAVER_SMTP_USER")
        smtp_password = os.getenv("NAVER_SMTP_PASSWORD")
        recipients = [r.strip() for r in os.getenv("EMAIL_RECIPIENTS", "").split(",") if r.strip()]

        if not smtp_user or not smtp_password or not recipients:
            print("이메일 설정이 없어 시스템 알림 발송 불가")
            return False

        full_subject = f"[{site_name.upper()} 시스템 알림] {subject}"
        full_body = (
            f"=== {site_name.upper()} 모니터링 시스템 알림 ===\n\n{body}\n\n"
            f"발송 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "※ 이 알림은 하루에 한 번만 발송됩니다."
        )

        msg = MIMEText(full_body, "plain", "utf-8")
        msg["Subject"] = full_subject
        msg["From"] = smtp_user
        msg["To"] = ", ".join(recipients)

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())

        alerts = load_system_alerts(site_name)
        alerts[alert_type] = {
            "last_sent": datetime.now().strftime("%Y-%m-%d"),
            "message": subject,
            "timestamp": datetime.now().isoformat(),
        }
        save_system_alerts(site_name, alerts)
        return True
    except Exception as e:
        print(f"{site_name} 시스템 알림 발송 실패: {e}")
        return False
