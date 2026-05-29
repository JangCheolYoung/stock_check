#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
서버 헬스 모니터 — stock_check 운영용.

모드:
  --mode resource   : CPU/메모리/디스크 임계 체크 (기본 5분 단위 systemd timer)
                      임계 초과 시 텔레그램+이메일로 알림. 같은 종류는 60분 쿨다운.
  --mode daily      : 매일 1회 헬스 요약 (기본 매일 06:00 KST timer)
                      서비스 상태 + 자원 + 오늘 사이클 통계를 텔레그램+이메일 발송.

환경변수(.env):
  HEALTH_CPU_THRESHOLD       기본 80 (%)
  HEALTH_MEM_THRESHOLD       기본 80 (%)
  HEALTH_DISK_THRESHOLD      기본 85 (%)
  HEALTH_RESOURCE_COOLDOWN_MIN  기본 60 (분)
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID                  알림 텔레그램
  SMTP_SERVER / SMTP_PORT / SMTP_USER / SMTP_PASSWORD    이메일 (네이버용 NAVER_* 도 호환)
  EMAIL_RECIPIENTS                                       콤마 구분
"""
from __future__ import annotations

import argparse
import os
import shutil
import smtplib
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

try:
    import psutil
except ImportError:
    print("[health] psutil 미설치 — `pip install psutil` 필요", file=sys.stderr)
    sys.exit(2)

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / "stock_check" / "shared" / ".env")
load_dotenv()

DATA_ROOT = Path(os.getenv("STOCK_CHECK_DATA_ROOT", str(PROJECT_ROOT / "stock_check")))
STATE_DIR = DATA_ROOT / "shared" / "health_state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

CPU_TH = float(os.getenv("HEALTH_CPU_THRESHOLD", "80"))
MEM_TH = float(os.getenv("HEALTH_MEM_THRESHOLD", "80"))
DISK_TH = float(os.getenv("HEALTH_DISK_THRESHOLD", "85"))
COOLDOWN_MIN = int(os.getenv("HEALTH_RESOURCE_COOLDOWN_MIN", "60"))

# 알림 채널 선택. 기본 telegram,email. 텔레그램만: HEALTH_NOTIFY_CHANNELS=telegram
_CHANNELS = {
    c.strip().lower()
    for c in os.getenv("HEALTH_NOTIFY_CHANNELS", "telegram,email").split(",")
    if c.strip()
}

HOSTNAME = socket.gethostname()


def _now_kst():
    """리포트 표기는 항상 Asia/Seoul."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        return datetime.now()


def _telegram_enabled() -> bool:
    return "telegram" in _CHANNELS


def _email_enabled() -> bool:
    return "email" in _CHANNELS


# ---------------------------------------------------------------- 발송 helper

def telegram_send(text: str) -> bool:
    if not _telegram_enabled():
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as exc:
        print(f"[health] 텔레그램 발송 실패: {exc}", file=sys.stderr)
        return False


def email_send(subject: str, body: str) -> bool:
    if not _email_enabled():
        return False
    user = os.getenv("SMTP_USER") or os.getenv("NAVER_SMTP_USER")
    pwd = os.getenv("SMTP_PASSWORD") or os.getenv("NAVER_SMTP_PASSWORD")
    server = os.getenv("SMTP_SERVER") or os.getenv("NAVER_SMTP_SERVER", "smtp.naver.com")
    port = int(os.getenv("SMTP_PORT") or os.getenv("NAVER_SMTP_PORT") or "587")
    rcpts = [r.strip() for r in os.getenv("EMAIL_RECIPIENTS", "").split(",") if r.strip()]
    if not user or not pwd or not rcpts:
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"[stock-check][{HOSTNAME}] {subject}"
        msg["From"] = user
        msg["To"] = ", ".join(rcpts)
        with smtplib.SMTP(server, port) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(user, rcpts, msg.as_string())
        return True
    except Exception as exc:
        print(f"[health] 이메일 발송 실패: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------- cooldown

def can_send_critical(tag: str) -> bool:
    path = STATE_DIR / f"crit_{tag}.last"
    if not path.exists():
        return True
    try:
        last = datetime.fromisoformat(path.read_text().strip())
    except Exception:
        return True
    return datetime.now() - last >= timedelta(minutes=COOLDOWN_MIN)


def mark_critical(tag: str) -> None:
    (STATE_DIR / f"crit_{tag}.last").write_text(datetime.now().isoformat())


# ---------------------------------------------------------------- collectors

def collect_resources() -> dict:
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    return {
        "cpu_pct": cpu,
        "mem_pct": mem.percent,
        "mem_used_mb": int(mem.used / (1024 * 1024)),
        "mem_total_mb": int(mem.total / (1024 * 1024)),
        "disk_pct": disk.used * 100 / disk.total,
        "disk_used_gb": int(disk.used / (1024 ** 3)),
        "disk_total_gb": int(disk.total / (1024 ** 3)),
    }


def service_status(unit: str) -> str:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def recent_cycle_stats(site: str) -> dict:
    """가장 최근 mtime 의 사이트 로그 파일에서 사이클 통계를 집계.

    이전엔 datetime.now() 로 '오늘 날짜' 파일을 찾았는데, scheduler 프로세스의
    TZ(UTC) 와 health daily 서비스의 TZ(Asia/Seoul)가 어긋나면 KST 자정~오전
    시간대에 파일을 못 찾는 문제가 있었음. 최신 mtime 기준으로 바꿔 TZ 차이에
    무관하게 동작.
    """
    log_dir = DATA_ROOT / site / "logs"
    stats = {"cycles": 0, "errors": 0, "last_summary": "", "file": ""}
    if not log_dir.is_dir():
        return stats
    try:
        files = sorted(
            (p for p in log_dir.glob("log-*.txt") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return stats
    if not files:
        return stats
    latest = files[0]
    stats["file"] = latest.name
    try:
        for line in latest.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "재고 확인 완료" in line and "소요시간" in line:
                stats["cycles"] += 1
                stats["last_summary"] = line[-220:]
            if "[ERROR" in line:
                stats["errors"] += 1
    except Exception:
        pass
    return stats


def site_cycle_lines() -> list[str]:
    lines = []
    for site, label in [("hyundai", "더현대"), ("cultizm", "컬티즘")]:
        cycle = recent_cycle_stats(site)
        lines.append(
            f"- {label}({cycle.get('file') or '?'}) : "
            f"{cycle['cycles']}회 / 오류 라인 {cycle['errors']}건"
        )
        lines.append(f"  마지막 로그: {cycle['last_summary'] or '기록 없음'}")
    return lines


# 하위호환 alias (혹시 외부 호출이 있을 경우)
today_cycle_stats = lambda: recent_cycle_stats("hyundai")


# ---------------------------------------------------------------- modes

def mode_resource() -> int:
    r = collect_resources()
    triggers: list[tuple[str, str]] = []
    if r["cpu_pct"] >= CPU_TH:
        triggers.append(("cpu", f"CPU {r['cpu_pct']:.1f}% ≥ {CPU_TH}%"))
    if r["mem_pct"] >= MEM_TH:
        triggers.append((
            "mem",
            f"메모리 {r['mem_pct']:.1f}% ({r['mem_used_mb']}/{r['mem_total_mb']}MB) ≥ {MEM_TH}%",
        ))
    if r["disk_pct"] >= DISK_TH:
        triggers.append((
            "disk",
            f"디스크 / {r['disk_pct']:.1f}% ({r['disk_used_gb']}/{r['disk_total_gb']}GB) ≥ {DISK_TH}%",
        ))

    if not triggers:
        print(f"[health] OK cpu={r['cpu_pct']:.1f}% mem={r['mem_pct']:.1f}% disk={r['disk_pct']:.1f}%")
        return 0

    sent: list[str] = []
    for tag, line in triggers:
        if not can_send_critical(tag):
            print(f"[health] cooldown 중: {tag}")
            continue
        body = (
            f"⚠️ [{HOSTNAME}] 자원 임계 초과\n\n"
            f"{line}\n\n"
            f"현재 자원 사용량:\n"
            f"- CPU      : {r['cpu_pct']:.1f}%\n"
            f"- 메모리   : {r['mem_pct']:.1f}% ({r['mem_used_mb']}/{r['mem_total_mb']}MB)\n"
            f"- 디스크 / : {r['disk_pct']:.1f}% ({r['disk_used_gb']}/{r['disk_total_gb']}GB)\n\n"
            f"다음 {COOLDOWN_MIN}분간 동일 종류 알림은 억제됩니다."
        )
        ok_tg = telegram_send(body)
        ok_em = email_send(f"자원 임계 초과 — {tag.upper()}", body)
        if ok_tg or ok_em:
            mark_critical(tag)
            sent.append(tag)
    print(f"[health] resource triggers={[t for t,_ in triggers]} sent={sent}")
    return 0


def mode_daily() -> int:
    r = collect_resources()
    admin = service_status("stock-check-admin")
    cultizm_timer = service_status("stock-check-cultizm-scheduler.timer")
    hyundai_timer = service_status("stock-check-hyundai-scheduler.timer")
    legacy_timer = service_status("stock-check-scheduler.timer")
    cycle_lines = "\n".join(site_cycle_lines())

    body = (
        f"📊 [{HOSTNAME}] stock-check 일일 헬스 리포트\n"
        f"기준 시각: {_now_kst().strftime('%Y-%m-%d %H:%M:%S')} KST\n\n"
        f"서비스 상태:\n"
        f"- stock-check-admin       : {admin}\n"
        f"- stock-check-cultizm     : {cultizm_timer}\n"
        f"- stock-check-hyundai     : {hyundai_timer}\n"
        f"- stock-check-scheduler   : {legacy_timer}\n\n"
        f"자원 사용:\n"
        f"- CPU      : {r['cpu_pct']:.1f}%\n"
        f"- 메모리   : {r['mem_pct']:.1f}% ({r['mem_used_mb']}/{r['mem_total_mb']}MB)\n"
        f"- 디스크 / : {r['disk_pct']:.1f}% ({r['disk_used_gb']}/{r['disk_total_gb']}GB)\n\n"
        f"최근 로그:\n"
        f"{cycle_lines}\n"
    )
    ok_tg = telegram_send(body)
    ok_em = email_send("일일 헬스 리포트", body)
    print(f"[health] daily telegram={ok_tg} email={ok_em}")
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="stock-check 서버 헬스 모니터")
    ap.add_argument("--mode", choices=["resource", "daily"], required=True)
    args = ap.parse_args()
    if args.mode == "resource":
        return mode_resource()
    return mode_daily()


if __name__ == "__main__":
    sys.exit(main())
