#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_check/hyundai/stock_checker.py
더현대닷컴 재고 확인 스크립트 (안정성 강화 버전)

핵심 개선
- ✅ 타겟(상품) 1개 처리마다 WebDriver 생성/종료 (세션 먹통/localhost timeout 방지)
- ✅ MAX_WORKERS 기본 1 (멀티 워커는 옵션)
- ✅ 작업 단위 재시도(검색/클릭) + 명확한 타임아웃
- ✅ 드라이버/프로세스 종료 보장
- ✅ 락 파일 + 시그널 종료 시 락 제거
"""

import sys
import os
import time
import json
import signal
import threading
import traceback
from pathlib import Path
from datetime import datetime
import concurrent.futures
import warnings
warnings.filterwarnings("ignore", message="urllib3 .* chardet .* doesn't match a supported version")


from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# 공통 모듈 경로 추가
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))
from stock_check.shared.email_utils import send_stock_alert, send_system_alert

# 통합 환경변수 로드
load_dotenv(PROJECT_ROOT / 'stock_check' / 'shared' / '.env')
load_dotenv()

# =========================
# 설정
# =========================
DATA_ROOT = Path(os.getenv('STOCK_CHECK_DATA_ROOT', str(PROJECT_ROOT / 'stock_check')))
BASE_DIR = DATA_ROOT / 'hyundai'
TARGET_FILE = BASE_DIR / 'targets.txt'
LOG_DIR = BASE_DIR / 'logs'
LOCK_FILE = BASE_DIR / 'stock_checker.lock'

os.makedirs(LOG_DIR, exist_ok=True)

# 로그 동기화 락
log_lock = threading.Lock()

# =========================
# 로그
# =========================
def get_log_file():
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"log-{today}.txt")

def log(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    level_icons = {
        "INFO": "ℹ️",
        "SUCCESS": "✅",
        "ERROR": "❌",
        "WARNING": "⚠️",
        "DEBUG": "🔍"
    }
    icon = level_icons.get(level, "•")
    log_message = f"[{timestamp}] {icon} [{level:7s}] {message}"

    debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"

    with log_lock:
        if debug_mode:
            print(log_message)
        elif level in ["SUCCESS", "ERROR", "WARNING"]:
            print(log_message)

        try:
            with open(get_log_file(), "a", encoding="utf-8") as f:
                f.write(log_message + "\n")
        except:
            pass

# =========================
# 중복 실행 방지(락)
# =========================
def create_lock():
    try:
        if os.path.exists(LOCK_FILE):
            lock_age = time.time() - os.path.getmtime(LOCK_FILE)
            # 오래된 락은 제거 (기본 30분)
            stale_sec = int(os.getenv("LOCK_STALE_SEC", "1800"))
            if lock_age > stale_sec:
                os.remove(LOCK_FILE)
            else:
                return False

        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log(f"락 파일 생성 실패: {e}", "ERROR")
        return False

def remove_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except:
        pass

def _handle_exit(signum, frame):
    # 시그널 종료 시 락 제거
    try:
        log(f"시그널 종료 감지: {signum}", "WARNING")
    except:
        pass
    remove_lock()
    raise SystemExit(1)

signal.signal(signal.SIGTERM, _handle_exit)
signal.signal(signal.SIGINT, _handle_exit)

# =========================
# 리소스 체크(옵션)
# =========================
def check_system_resources():
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        memory_percent = psutil.virtual_memory().percent
        log(f"시스템 리소스 - CPU: {cpu_percent:.1f}%, 메모리: {memory_percent:.1f}%", "INFO")

        limit = float(os.getenv("RESOURCE_LIMIT_PERCENT", "80"))
        if cpu_percent > limit or memory_percent > limit:
            log("시스템 리소스 부족", "WARNING")
            return False
        return True

    except ImportError:
        return True
    except Exception as e:
        log(f"리소스 체크 실패: {e}", "INFO")
        return True

# =========================
# WebDriver 생성/종료
# =========================
def create_driver():
    chrome_options = Options()

    # headless 안정 모드
    # (크롬 최신이면 --headless=new 권장)
    headless_mode = os.getenv("CHROME_HEADLESS_MODE", "new").lower()
    if headless_mode == "new":
        chrome_options.add_argument("--headless=new")
    else:
        chrome_options.add_argument("--headless")

    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1280,720")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    # 이미지/알림 차단 (실효성 높음)
    chrome_options.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    })

    ua = os.getenv(
        "CHROME_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    chrome_options.add_argument(f"--user-agent={ua}")

    driver_path = os.getenv("CHROME_DRIVER_PATH", "/usr/local/bin/chromedriver")
    service = ChromeService(executable_path=driver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)

    # 타임아웃
    driver.set_page_load_timeout(int(os.getenv("PAGELOAD_TIMEOUT_SEC", "25")))
    driver.implicitly_wait(int(os.getenv("IMPLICIT_WAIT_SEC", "2")))

    return driver

def safe_quit_driver(driver):
    if not driver:
        return
    try:
        driver.quit()
    except:
        # 드물게 quit()가 멈추면 강제 종료 시도
        try:
            driver.service.process.kill()
        except:
            pass

def wait_for_ready(driver, timeout_sec=8):
    try:
        WebDriverWait(driver, timeout_sec).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(0.2)
    except:
        pass

# =========================
# 타겟 로드
# =========================
def load_targets():
    targets = []
    try:
        with open(TARGET_FILE, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" not in line:
                    continue

                keyword, sizes_str = line.split(":", 1)
                sizes = [s.strip() for s in sizes_str.split(",") if s.strip()]
                if not sizes:
                    continue

                targets.append({"keyword": keyword.strip(), "sizes": sizes, "line_num": line_num})

        return targets
    except Exception as e:
        log(f"targets.txt 로드 실패: {e}", "ERROR")
        return []

# =========================
# 현대 사이트 동작
# =========================
HYUNDAI_HOME = "https://www.thehyundai.com/front/dpa/dpaShopHome.thd"

def search_product(driver, keyword):
    """
    홈 진입 → 검색창 입력 → 검색 결과 페이지 진입
    """
    try:
        log(f"{keyword} 검색 시작", "DEBUG")

        driver.get(HYUNDAI_HOME)
        wait_for_ready(driver, timeout_sec=int(os.getenv("READY_TIMEOUT_SEC", "8")))

        search_box = WebDriverWait(driver, int(os.getenv("WAIT_SEARCHBOX_SEC", "10"))).until(
            EC.presence_of_element_located((By.ID, "cs-token-input"))
        )

        search_box.clear()
        search_box.send_keys(keyword)
        search_box.send_keys(Keys.RETURN)

        wait_for_ready(driver, timeout_sec=int(os.getenv("READY_TIMEOUT_SEC", "8")))
        return True

    except Exception as e:
        log(f"검색 실패 ({keyword}): {e}", "ERROR")
        return False

def click_first_product(driver):
    """
    검색 결과 첫 상품 클릭
    """
    try:
        products = WebDriverWait(driver, int(os.getenv("WAIT_PRODUCTS_SEC", "12"))).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".prod-unit"))
        )
        if not products:
            return False

        first_product = products[0]

        # 제목 링크 탐색
        title_link = None
        for selector in ("a.title.ellipsis", "a.title", "a"):
            try:
                title_link = first_product.find_element(By.CSS_SELECTOR, selector)
                if title_link:
                    break
            except:
                continue
        if not title_link:
            return False

        driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});",
            title_link
        )
        time.sleep(0.2)

        # JS click
        driver.execute_script("arguments[0].click();", title_link)
        wait_for_ready(driver, timeout_sec=int(os.getenv("DETAIL_READY_TIMEOUT_SEC", "15")))
        return True

    except TimeoutException:
        return False
    except Exception as e:
        log(f"첫 번째 상품 클릭 실패: {e}", "DEBUG")
        return False

def get_available_options(driver):
    """
    옵션 레이어에서 옵션명 수집
    """
    try:
        option_elements = WebDriverWait(driver, int(os.getenv("WAIT_OPTIONS_SEC", "15"))).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "ul.opt-select-layer ul.depth-opt-list li a span.opt-name")
            )
        )
        if not option_elements:
            return []

        options = []
        for opt in option_elements:
            try:
                text = (opt.get_attribute("textContent") or "").strip()
                if text:
                    options.append(text)
            except:
                continue
        return options

    except TimeoutException:
        return []
    except Exception as e:
        log(f"옵션 로드 실패: {e}", "DEBUG")
        return []

# =========================
# 텔레그램 알림 (requests 직접 전송 + 재시도)
# =========================
def send_telegram_alert(product, sizes, url):
    """
    기존처럼 subprocess로 분리해도 되지만,
    안정성/관측성을 위해 여기서 직접 requests로 발송 (재시도 포함)
    """
    try:
        import requests

        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            return False

        # 중복 방지
        telegram_interval = int(os.getenv("TELEGRAM_ALERT_INTERVAL", "3600"))  # 기본 1시간
        history_file = os.path.join(BASE_DIR, "telegram_history.json")

        try:
            if os.path.exists(history_file):
                with open(history_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
            else:
                history = []
        except:
            history = []

        now_ts = time.time()
        for record in history:
            if record.get("site") == "hyundai" and record.get("product") == product:
                last_sent = record.get("timestamp", 0)
                if now_ts - last_sent < telegram_interval:
                    remaining = telegram_interval - (now_ts - last_sent)
                    log(f"텔레그램 중복 방지: {product} (남은 {remaining/60:.1f}분)", "INFO")
                    return False

        repeat_count = int(os.getenv("TELEGRAM_REPEAT_COUNT", "3"))
        interval = float(os.getenv("TELEGRAM_INTERVAL", "2.0"))

        msg = f"🔔 재고 알림!\n\n상품: {product}\n사이즈: {', '.join(sizes)}\n\n{url}"

        endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        ok = False
        for i in range(repeat_count):
            try:
                r = requests.post(
                    endpoint,
                    json={"chat_id": chat_id, "text": msg, "disable_web_page_preview": False},
                    timeout=8
                )
                if r.status_code == 200:
                    ok = True
                    break
            except:
                pass
            time.sleep(interval)

        if not ok:
            return False

        # 이력 저장
        history.append({
            "site": "hyundai",
            "product": product,
            "sizes": sizes,
            "timestamp": now_ts,
            "datetime": datetime.now().isoformat()
        })

        week_ago = now_ts - (7 * 24 * 3600)
        history = [h for h in history if h.get("timestamp", 0) > week_ago]

        try:
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except:
            pass

        return True

    except Exception:
        return False

# =========================
# 단일 타겟 처리
# =========================
def check_single_stock(target):
    """
    타겟 1개 처리:
    - ✅ 드라이버를 여기서 생성하고 여기서 종료 (재사용 X)
    - ✅ 단계별 재시도
    """
    keyword = target["keyword"]
    target_sizes = target["sizes"]

    start_time = time.time()
    driver = None

    # 단계 재시도 횟수
    step_retries = int(os.getenv("STEP_RETRIES", "2"))

    try:
        driver = create_driver()

        # 1) 검색 (재시도)
        searched = False
        for i in range(step_retries + 1):
            if search_product(driver, keyword):
                searched = True
                break
            # 검색 실패 시 드라이버를 새로 만들어 재시도(세션 꼬임 방지)
            safe_quit_driver(driver)
            driver = create_driver()
            time.sleep(0.5)

        if not searched:
            log(f"{keyword} - 검색 실패", "ERROR")
            return {"status": StockStatus.SEARCH_FAILED.value, "product": keyword}

        # 2) 첫 상품 클릭 (재시도)
        clicked = False
        for i in range(step_retries + 1):
            if click_first_product(driver):
                clicked = True
                break
            time.sleep(0.4)

        if not clicked:
            log(f"{keyword} - 검색 결과 없음/클릭 실패", "INFO")
            return {"status": StockStatus.PRODUCT_NOT_FOUND.value, "product": keyword}

        # 3) 옵션 수집
        available_options = get_available_options(driver)
        if not available_options:
            log(f"{keyword} - 옵션 없음", "INFO")
            return {"status": StockStatus.OUT_OF_STOCK.value, "product": keyword}

        # 4) 사이즈 매칭
        matched_sizes = []
        for option in available_options:
            option_clean = option.replace(" ", "").lower()
            for target_size in target_sizes:
                target_clean = target_size.replace(" ", "").lower()
                if target_clean in option_clean:
                    matched_sizes.append(target_size)
                    break

        elapsed = time.time() - start_time

        if matched_sizes:
            log(
                f"{keyword} - 재고 확인 성공 | 타겟: [{', '.join(target_sizes)}] | "
                f"재고: [{', '.join(available_options)}] | 매칭: [{', '.join(matched_sizes)}] ({elapsed:.1f}초)",
                "SUCCESS"
            )
            return {"status": StockStatus.IN_STOCK.value, "product": keyword, "sizes": matched_sizes, "url": driver.current_url}

        log(
            f"{keyword} - 재고 없음 | 타겟: [{', '.join(target_sizes)}] | 재고: [{', '.join(available_options)}] ({elapsed:.1f}초)",
            "INFO"
        )
        return {"status": StockStatus.OUT_OF_STOCK.value, "product": keyword, "available_options": available_options}

    except Exception as e:
        elapsed = time.time() - start_time
        log(f"{keyword} - 오류 발생: {e} ({elapsed:.1f}초)", "ERROR")
        return {"status": StockStatus.UNKNOWN_ERROR.value, "product": keyword, "error": str(e)}

    finally:
        safe_quit_driver(driver)

# =========================
# 메인
# =========================
def main():
    if not create_lock():
        log("이미 실행 중입니다", "WARNING")
        return

    start_time = time.time()

    try:
        log("=" * 60, "INFO")
        log("더현대닷컴 재고 확인 시작", "INFO")
        log("=" * 60, "INFO")

        targets = load_targets()
        if not targets:
            log("검색 대상이 없습니다", "ERROR")
            return

        log(f"검색 타겟: {len(targets)}개", "INFO")

        # t4g.small 운영 정책: 단일 워커 고정 (코어 1개 사용)
        if not check_system_resources():
            log("리소스 체크 경고가 있지만 단일 워커 정책 유지", "WARNING")

        max_workers = 1
        log("워커 수 고정: 1", "INFO")

        # 전체 타임아웃(초) - 기본 12분
        overall_timeout = int(os.getenv("OVERALL_TIMEOUT_SEC", "720"))

        search_results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_target = {
                executor.submit(check_single_stock, t): t for t in targets
            }

            # as_completed에도 전체 제한을 걸어두면 “영원히 대기” 방지
            for future in concurrent.futures.as_completed(future_to_target, timeout=overall_timeout):
                t = future_to_target[future]
                # 작업 단위 타임아웃(초) - 기본 150초
                per_task_timeout = int(os.getenv("PER_TASK_TIMEOUT_SEC", "150"))
                try:
                    result = future.result(timeout=per_task_timeout)
                    if result:
                        search_results.append(result)
                except concurrent.futures.TimeoutError:
                    log(f"타임아웃: {t['keyword']} (per_task_timeout={per_task_timeout}s)", "ERROR")
                    search_results.append({"status": StockStatus.PAGE_ERROR.value, "product": t["keyword"]})
                except Exception as e:
                    log(f"Future 오류: {t['keyword']} | {e}", "ERROR")
                    search_results.append({"status": StockStatus.UNKNOWN_ERROR.value, "product": t["keyword"], "error": str(e)})

        alert_policy = AlertPolicy("hyundai")

        # 결과 분석
        available_items = []
        system_errors = []

        for r in search_results:
            status = r.get("status", "unknown")
            if status == StockStatus.IN_STOCK.value:
                available_items.append({
                    "product": r["product"],
                    "sizes": r["sizes"],
                    "url": r["url"]
                })
            elif status in [StockStatus.SEARCH_FAILED.value, StockStatus.UNKNOWN_ERROR.value, StockStatus.PAGE_ERROR.value]:
                system_errors.append(r)
                alert_policy.record_ops_status(r.get("product", "unknown"), {
                    "last_status": status,
                    "last_message": r.get("error", "crawler error"),
                    "is_error": True,
                })

        elapsed_time = time.time() - start_time

        # 오류율 체크 -> 시스템 알림
        total = len(search_results)
        err_cnt = len(system_errors)
        err_rate = (err_cnt / total * 100) if total else 0

        if err_rate >= 50 and total >= 3:
            try:
                send_system_alert(
                    "hyundai", "high_error_rate",
                    f"높은 오류율 ({err_rate:.1f}%)",
                    f"전체: {total}개\n오류: {err_cnt}개\n재고: {len(available_items)}개\n소요: {elapsed_time:.1f}초"
                )
            except Exception as e:
                log(f"hyundai 시스템 알림 발송 실패: {e}", "ERROR")

        # 재고 알림
        if available_items:
            email_sent = 0
            telegram_sent = 0

            for item in available_items:
                product = item["product"]
                sizes = item["sizes"]
                url = item["url"]

                dedup_prefix = alert_policy.make_dedup_key(product, "ALL", StockStatus.IN_STOCK.value)
                policy_mode = os.getenv("ALERT_POLICY_MODE", "v1")
                decision = alert_policy.should_send(dedup_prefix, policy_mode=policy_mode)
                if not decision.should_send:
                    log(f"알림 스킵: {product} ({decision.reason})", "DEBUG")
                    continue

                try:
                    if send_stock_alert("hyundai", product, sizes, url, dedup_prefix=dedup_prefix):
                        email_sent += 1
                except Exception as e:
                    log(f"이메일 알림 실패: {product} | {e}", "ERROR")

                try:
                    if send_telegram_alert(product, sizes, url):
                        telegram_sent += 1
                except Exception as e:
                    log(f"텔레그램 알림 실패: {product} | {e}", "ERROR")

                alert_policy.mark_sent(dedup_prefix, StockStatus.IN_STOCK.value)
                alert_policy.record_ops_status(product, {
                    "last_status": StockStatus.IN_STOCK.value,
                    "last_message": f"sizes={','.join(sizes)}",
                    "product_url": url,
                    "is_error": False,
                })

            log(
                f"재고 확인 완료 | 재고: {len(available_items)}개 | 이메일: {email_sent}개 | "
                f"텔레그램: {telegram_sent}개 | 소요시간: {elapsed_time:.1f}초",
                "SUCCESS"
            )

            # 최근 결과 저장
            result_blob = {
                "timestamp": datetime.now().isoformat(),
                "execution_time": f"{elapsed_time:.1f}s",
                "total_checked": len(targets),
                "available_count": len(available_items),
                "email_sent": email_sent,
                "telegram_sent": telegram_sent,
                "threads_used": max_workers,
                "available_items": [{"product": i["product"], "sizes": i["sizes"]} for i in available_items]
            }

            results_file = os.path.join(BASE_DIR, "recent_results.json")
            try:
                if os.path.exists(results_file):
                    with open(results_file, "r", encoding="utf-8") as f:
                        recent_results = json.load(f)
                else:
                    recent_results = []
            except:
                recent_results = []

            try:
                recent_results.insert(0, result_blob)
                recent_results = recent_results[:5]
                with open(results_file, "w", encoding="utf-8") as f:
                    json.dump(recent_results, f, indent=2, ensure_ascii=False)
            except:
                pass

        else:
            log(
                f"재고 확인 완료 | 재고 없음 | 검색: {len(targets)}개 | 오류: {err_cnt}개 | 소요시간: {elapsed_time:.1f}초",
                "INFO"
            )

    except concurrent.futures.TimeoutError:
        # as_completed timeout
        elapsed_time = time.time() - start_time
        log(f"전체 프로세스 타임아웃 (overall_timeout 초과) | 소요: {elapsed_time:.1f}초", "ERROR")

    except Exception as e:
        elapsed_time = time.time() - start_time
        log(f"전체 프로세스 오류: {e} | 소요: {elapsed_time:.1f}초", "ERROR")
        # 디버그 모드면 traceback 기록
        if os.getenv("DEBUG_MODE", "false").lower() == "true":
            log(traceback.format_exc(), "DEBUG")

    finally:
        remove_lock()
        elapsed_time = time.time() - start_time
        log("=" * 60, "INFO")
        log(f"더현대닷컴 재고 확인 완료 (소요시간: {elapsed_time:.1f}초)", "INFO")
        log("=" * 60, "INFO")

if __name__ == "__main__":
    main()

