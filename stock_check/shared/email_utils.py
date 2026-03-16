#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_check/shared/email_utils.py
공통 이메일 발송 모듈
"""

import smtplib
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from dotenv import load_dotenv

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / "stock_check"

load_dotenv(CURRENT_DIR / ".env")
load_dotenv()


def _site_dir(site_name):
    data_root = Path(os.getenv("STOCK_CHECK_DATA_ROOT", str(DEFAULT_DATA_ROOT)))
    return data_root / site_name

def load_email_history(site_name):
    """사이트별 메일 발송 기록 로드"""
    try:
        history_file = _site_dir(site_name) / "email_history.json"
        if history_file.exists():
            with open(history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    except:
        return {}

def save_email_history(site_name, history):
    """사이트별 메일 발송 기록 저장"""
    try:
        history_file = _site_dir(site_name) / "email_history.json"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"메일 기록 저장 실패: {e}")

def can_send_email(site_name, product, size):
    """해당 제품:사이즈 조합에 대해 설정된 시간 내 메일 발송 가능한지 확인"""
    history = load_email_history(site_name)
    key = f"{product}:{size}"
    
    if key not in history:
        return True
    
    last_sent = datetime.fromisoformat(history[key])
    time_diff = datetime.now() - last_sent
    
    # 환경변수에서 이메일 알림 간격 읽기 (초 단위, 기본값 3600초=1시간)
    email_interval_seconds = int(os.getenv("EMAIL_ALERT_INTERVAL", "3600"))
    
    return time_diff >= timedelta(seconds=email_interval_seconds)

def record_email_sent(site_name, product, size):
    """메일 발송 기록 저장"""
    history = load_email_history(site_name)
    key = f"{product}:{size}"
    history[key] = datetime.now().isoformat()
    
    # 1주일 이상 된 기록은 삭제
    week_ago = datetime.now() - timedelta(days=7)
    history = {k: v for k, v in history.items() 
              if datetime.fromisoformat(v) > week_ago}
    
    save_email_history(site_name, history)

def send_stock_alert(site_name, product, sizes, url):
    """재고 알림 메일 발송"""
    try:
        smtp_server = os.getenv("NAVER_SMTP_SERVER", "smtp.naver.com")
        smtp_port = int(os.getenv("NAVER_SMTP_PORT", "587"))
        smtp_user = os.getenv("NAVER_SMTP_USER")
        smtp_password = os.getenv("NAVER_SMTP_PASSWORD")
        recipients = os.getenv("EMAIL_RECIPIENTS", "").split(",")

        if not smtp_user or not smtp_password:
            print("이메일 설정이 없습니다")
            return False

        # 환경변수에서 이메일 알림 간격 읽기
        email_interval_seconds = int(os.getenv("EMAIL_ALERT_INTERVAL", "3600"))
        interval_hours = email_interval_seconds / 3600

        # 발송 가능한 사이즈만 필터링
        sendable_sizes = []
        for size in sizes:
            if can_send_email(site_name, product, size):
                sendable_sizes.append(size)
            else:
                print(f"{site_name} {product}:{size} - {interval_hours:.1f}시간 내 발송됨, 스킵")

        if not sendable_sizes:
            print(f"{site_name} {product} - 모든 사이즈가 {interval_hours:.1f}시간 내 발송됨")
            return False

        # 메일 내용 작성
        subject = f"[{site_name.upper()} 재입고 알림] {product} - {', '.join(sendable_sizes)} 재고 확인됨"
        
        body = f"=== {site_name.upper()} 재고 확인 결과 ===\n\n"
        body += f"상품: {product}\n"
        body += f"사용가능 사이즈: {', '.join(sendable_sizes)}\n"
        body += f"링크: {url}\n\n"
        body += f"확인 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = ", ".join(recipients)

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
        
        # 발송된 사이즈들 기록
        for size in sendable_sizes:
            record_email_sent(site_name, product, size)
        
        return True
        
    except Exception as e:
        print(f"이메일 발송 실패: {e}")
        return False

def load_system_alerts(site_name):
    """시스템 알림 기록 로드"""
    try:
        alert_file = _site_dir(site_name) / "system_alerts.json"
        if alert_file.exists():
            with open(alert_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    except:
        return {}

def save_system_alerts(site_name, alerts):
    """시스템 알림 기록 저장"""
    try:
        alert_file = _site_dir(site_name) / "system_alerts.json"
        alert_file.parent.mkdir(parents=True, exist_ok=True)
        with open(alert_file, "w", encoding="utf-8") as f:
            json.dump(alerts, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"시스템 알림 기록 저장 실패: {e}")

def can_send_system_alert(site_name, alert_type):
    """시스템 알림을 보낼 수 있는지 확인 (하루에 한 번만)"""
    alerts = load_system_alerts(site_name)
    today = datetime.now().strftime("%Y-%m-%d")
    
    if alert_type not in alerts:
        return True
    
    last_sent = alerts[alert_type].get('last_sent', '')
    return last_sent != today

def send_system_alert(site_name, alert_type, subject, body):
    """시스템 알림 발송"""
    if not can_send_system_alert(site_name, alert_type):
        print(f"{site_name} 시스템 알림 스킵 (오늘 이미 발송됨): {alert_type}")
        return False
    
    try:
        smtp_server = os.getenv("NAVER_SMTP_SERVER", "smtp.naver.com")
        smtp_port = int(os.getenv("NAVER_SMTP_PORT", "587"))
        smtp_user = os.getenv("NAVER_SMTP_USER")
        smtp_password = os.getenv("NAVER_SMTP_PASSWORD")
        recipients = os.getenv("EMAIL_RECIPIENTS", "").split(",")

        if not smtp_user or not smtp_password:
            print("이메일 설정이 없어 시스템 알림 발송 불가")
            return False

        full_subject = f"[{site_name.upper()} 시스템 알림] {subject}"
        full_body = f"=== {site_name.upper()} 모니터링 시스템 알림 ===\n\n{body}\n\n발송 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n※ 이 알림은 하루에 한 번만 발송됩니다."

        msg = MIMEText(full_body, "plain", "utf-8")
        msg["Subject"] = full_subject
        msg["From"] = smtp_user
        msg["To"] = ", ".join(recipients)

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
        
        # 발송 기록
        alerts = load_system_alerts(site_name)
        today = datetime.now().strftime("%Y-%m-%d")
        alerts[alert_type] = {
            'last_sent': today,
            'message': subject,
            'timestamp': datetime.now().isoformat()
        }
        save_system_alerts(site_name, alerts)
        
        print(f"{site_name} 시스템 알림 발송 성공: {alert_type}")
        return True
        
    except Exception as e:
        print(f"{site_name} 시스템 알림 발송 실패: {e}")
        return False
