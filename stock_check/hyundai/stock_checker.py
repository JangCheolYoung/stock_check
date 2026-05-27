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
import re
import time
import json
import signal
import threading
import traceback
import builtins
from pathlib import Path
from datetime import datetime
import concurrent.futures
import warnings
warnings.filterwarnings("ignore", message="urllib3 .* chardet .* doesn't match a supported version")
# macOS 기본 LibreSSL 2.8.3 환경에서 urllib3 v2 가 매번 출력하는 경고 묻기
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
try:
    from urllib3.exceptions import NotOpenSSLWarning  # type: ignore
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except Exception:
    pass


from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

# 공통 모듈 경로 추가
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))
from stock_check.shared.email_utils import send_stock_alert, send_system_alert
from stock_check.shared.alert_policy import AlertPolicy

# 하위호환: 일부 배포본에서 StockStatus 참조 코드가 남아 있어 NameError가 발생하는 경우 방지
try:
    from stock_check.app.models import StockStatus  # noqa: F401
except Exception:
    class StockStatus:  # type: ignore
        IN_STOCK = "IN_STOCK"
        OUT_OF_STOCK = "OUT_OF_STOCK"
        PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
        SEARCH_FAILED = "SEARCH_FAILED"
        PAGE_ERROR = "PAGE_ERROR"
        BLOCKED = "BLOCKED"
        UNKNOWN_ERROR = "UNKNOWN_ERROR"

# 배포본 혼재(레거시 코드/동적 평가)로 전역 스코프에서 StockStatus를 못 찾는 경우까지 방지
builtins.StockStatus = StockStatus

def safe_load_dotenv(path=None, **kwargs):
    try:
        return load_dotenv(path, **kwargs) if path is not None else load_dotenv(**kwargs)
    except PermissionError:
        return False


# 통합 환경변수 로드
safe_load_dotenv(PROJECT_ROOT / 'stock_check' / 'shared' / '.env')
safe_load_dotenv()

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
def _now_local():
    """로그 출력은 항상 Asia/Seoul 기준."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        return datetime.now()


def get_log_file():
    today = _now_local().strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"log-{today}.txt")

def log(message, level="INFO"):
    timestamp = _now_local().strftime("%Y-%m-%d %H:%M:%S")

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
    # CHROME_HEADLESS_MODE: "new"(기본) | "old" | "off"/"0"/"false"/"no"
    # off 로 두면 실제 브라우저 창을 띄워 동작을 눈으로 확인할 수 있다(디버깅용).
    headless_mode = os.getenv("CHROME_HEADLESS_MODE", "new").lower()
    if headless_mode in ("off", "0", "false", "no", "none"):
        pass  # headless 끔
    elif headless_mode == "new":
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


def normalize_size_token(value: str) -> str:
    """사이즈 비교용 정규화: 대소문자/공백만 무시하고 문자열은 정확 일치 비교."""
    return value.replace(" ", "").lower()


def match_target_sizes(available_options: list[str], target_sizes: list[str]) -> list[str]:
    """타겟 사이즈를 정확 일치로 매칭하고 중복을 제거한다."""
    available_tokens = {normalize_size_token(option) for option in available_options}
    matched: list[str] = []
    seen: set[str] = set()
    for target_size in target_sizes:
        token = normalize_size_token(target_size)
        if token in available_tokens and token not in seen:
            matched.append(target_size)
            seen.add(token)
    return matched

# =========================
# 현대 사이트 동작 (리뉴얼 후: hi.thehyundai.com — Next.js SPA, 모바일 우선)
# =========================
HYUNDAI_HOME = os.getenv("HYUNDAI_ENTRY_URL", "https://hi.thehyundai.com/shop/main")

# 셀렉터 상수 (캡쳐 결과 기반, 향후 사이트가 또 바뀌면 이 블록만 갱신)
# 메인(/shop/main) 헤더는 SearchButton_root 버튼, 상세/에러 페이지는 aria-label="검색"
# IconButton 으로 헤더 디자인이 달라서 둘 다 폴백한다.
SEL_SEARCH_ENTRIES = [
    "button.SearchButton_root__exYZe",
    "[class*='SearchButton_root']",
    "header button[aria-label='검색']",
    "header [aria-label='검색']",
    "[class*='Icon_search']",
]
SEL_SEARCH_INPUT = "input[type='search']"
SEL_PRODUCT_CARD = "a[href*='/product/']"
SEL_BUY_BUTTON_TEXT = "구매하기"
SEL_BUY_BUTTON_CSS = "button.Button_primary__aI9o6.Button_large__EWW0F"
SEL_OPTION_DRAWER = "[class*='Drawer_root']"
SEL_SIZE_COMBOBOX = "div[role='combobox'][aria-label='사이즈']"
SEL_SIZE_LISTBOX = "ul#select-listbox"
SEL_SIZE_OPTION = "li.Select_option___q_RU, li[role='option']"
SEL_LOGIN_REQUIRED_HINT = "취소"  # 로그인 confirm 모달 본문

# H.Point 통합회원 로그인 페이지
HYUNDAI_LOGIN_URL = os.getenv("HYUNDAI_LOGIN_URL", "https://hi.thehyundai.com/login")
SEL_LOGIN_ID = "input[name='loginId']"
SEL_LOGIN_PW = "input[name='password']"

# 사이즈 옵션 행 1개의 텍스트에서 수량 추출
_QTY_RE = re.compile(r"\[남은수량\s*:\s*(\d+)\]")
_SOLDOUT_RE = re.compile(r"\[\s*품절\s*\]")


def _wait_clickable(driver, css: str, timeout: int = 10):
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, css))
    )


def _wait_search_entry(driver, timeout: int = 15):
    """SEL_SEARCH_ENTRIES 중 처음으로 visible 해지는 요소를 polling 으로 찾는다."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in SEL_SEARCH_ENTRIES:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                els = []
            for el in els:
                try:
                    if el.is_displayed():
                        return el, sel
                except Exception:
                    continue
        time.sleep(0.4)
    return None, None


def _dump_failure(driver, keyword: str, tag: str) -> None:
    """검색/구매/옵션 단계 실패 시 디버그용 page_source/스크린샷을 저장."""
    if os.getenv("HYUNDAI_DEBUG_DUMP", "true").lower() in ("0", "false", "no", "off"):
        return
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_kw = "".join(c if c.isalnum() else "_" for c in keyword)[:40]
        out_dir = Path(LOG_DIR) / "failures" / f"{ts}_{safe_kw}_{tag}"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            (out_dir / "url.txt").write_text(driver.current_url or "", encoding="utf-8")
        except Exception:
            pass
        try:
            (out_dir / "page.html").write_text(driver.page_source or "", encoding="utf-8")
        except Exception:
            pass
        try:
            driver.save_screenshot(str(out_dir / "screen.png"))
        except Exception:
            pass
        log(f"실패 덤프: {out_dir}", "WARNING")
    except Exception as exc:
        log(f"실패 덤프 자체가 실패: {exc}", "DEBUG")


def _try_click(driver, el) -> bool:
    """selenium native click → JS click → MouseEvent dispatch 폴백.

    StaleElementReferenceException 은 React SPA 에서 자주 발생하지만 caller
    가 polling 루프로 재발견·재시도하므로 로그를 남기지 않고 즉시 False 반환.
    그 외 예외(클릭 인터셉트, not-interactable 등)는 type 이름만 한 줄 요약.
    """
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'instant',block:'center'});", el
        )
        time.sleep(0.15)
    except Exception:
        pass
    last_errs: list[str] = []
    for kind in ("native", "js", "dispatch"):
        try:
            if kind == "native":
                el.click()
            elif kind == "js":
                driver.execute_script("arguments[0].click();", el)
            else:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new MouseEvent('click', "
                    "{bubbles:true, cancelable:true, view:window, button:0}));",
                    el,
                )
            return True
        except StaleElementReferenceException:
            return False
        except Exception as exc:
            last_errs.append(f"{kind}:{type(exc).__name__}")
            continue
    if last_errs:
        log("클릭 시도 모두 실패 → " + " | ".join(last_errs), "DEBUG")
    return False


def ensure_logged_in(driver) -> bool:
    """
    HYUNDAI_LOGIN_ID / HYUNDAI_LOGIN_PW 환경변수를 사용해 H.Point 통합회원
    로그인을 시도. 자격증명이 없으면 silently skip (False 반환).

    반환:
      True  — 로그인 성공 (URL 이 /login 에서 벗어남)
      False — 자격증명 없음 / 로그인 실패. 호출자는 비회원 흐름으로 진행.
    """
    user = os.getenv("HYUNDAI_LOGIN_ID")
    pw = os.getenv("HYUNDAI_LOGIN_PW")
    if not user or not pw:
        log("HYUNDAI_LOGIN_ID/PW 미설정 → 로그인 스킵", "INFO")
        return False

    try:
        driver.get(HYUNDAI_LOGIN_URL)
        wait_for_ready(driver, timeout_sec=10)
        time.sleep(float(os.getenv("HYDRATION_SLEEP_SEC", "2.0")))

        # 이미 로그인 상태라면 /login 에서 다른 페이지로 자동 리다이렉트됨
        if "/login" not in (driver.current_url or ""):
            log("이미 로그인 상태로 보임", "INFO")
            return True

        try:
            id_el = WebDriverWait(driver, int(os.getenv("WAIT_LOGIN_INPUT_SEC", "10"))).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SEL_LOGIN_ID))
            )
            pw_el = driver.find_element(By.CSS_SELECTOR, SEL_LOGIN_PW)
        except (TimeoutException, Exception) as exc:
            log(f"로그인 입력란을 찾지 못함: {exc}", "ERROR")
            _dump_failure(driver, "_", "login_input_missing")
            return False

        try:
            id_el.clear()
        except Exception:
            pass
        id_el.send_keys(user)
        try:
            pw_el.clear()
        except Exception:
            pass
        pw_el.send_keys(pw)

        # '로그인' 텍스트의 primary 버튼 클릭 (페이지에 같은 클래스의 다른
        # primary 버튼이 있을 수 있어 텍스트로 식별).
        login_btn = None
        try:
            for el in driver.find_elements(By.XPATH, "//button[normalize-space(.)='로그인']"):
                if el.is_displayed():
                    login_btn = el
                    break
        except Exception:
            login_btn = None
        if not login_btn:
            log("로그인 버튼을 찾지 못함", "ERROR")
            _dump_failure(driver, "_", "login_button_missing")
            return False

        if not _try_click(driver, login_btn):
            log("로그인 버튼 클릭 실패", "ERROR")
            _dump_failure(driver, "_", "login_button_click_failed")
            return False

        # 로그인 완료 대기 — URL 이 /login 을 벗어나면 성공으로 판정
        deadline = time.time() + int(os.getenv("WAIT_LOGIN_SEC", "20"))
        while time.time() < deadline:
            cur = driver.current_url or ""
            if "/login" not in cur:
                log(f"로그인 성공 (url={cur})", "SUCCESS")
                return True
            time.sleep(0.5)

        log(f"로그인 timeout — url 이 여전히 /login (url={driver.current_url})", "WARNING")
        _dump_failure(driver, "_", "login_timeout")
        return False

    except Exception as exc:
        log(f"로그인 흐름 예외: {exc}", "ERROR")
        _dump_failure(driver, "_", "login_exception")
        return False


def search_product(driver, keyword):
    """
    홈 진입 → 검색 아이콘 클릭 → 검색 입력창 입력 → 결과 페이지 진입
    리뉴얼 후 SPA 흐름.
    """
    try:
        log(f"{keyword} 검색 시작", "DEBUG")
        driver.get(HYUNDAI_HOME)
        wait_for_ready(driver, timeout_sec=int(os.getenv("READY_TIMEOUT_SEC", "10")))
        time.sleep(float(os.getenv("HYDRATION_SLEEP_SEC", "2.0")))  # SPA hydration

        # 1) 검색 진입 버튼 클릭 (페이지마다 헤더가 달라 다중 후보 폴백)
        icon, hit_sel = _wait_search_entry(driver, timeout=int(os.getenv("WAIT_SEARCH_ICON_SEC", "15")))
        if not icon:
            log(f"검색 진입 버튼을 찾지 못함 ({keyword}) url={driver.current_url}", "ERROR")
            _dump_failure(driver, keyword, "search_icon_missing")
            return False
        log(f"검색 진입 버튼 매치: {hit_sel}", "DEBUG")
        if not _try_click(driver, icon):
            log(f"검색 진입 버튼 클릭 실패 ({keyword})", "ERROR")
            _dump_failure(driver, keyword, "search_icon_click_failed")
            return False
        time.sleep(float(os.getenv("HYDRATION_SLEEP_SEC", "1.0")))
        wait_for_ready(driver, timeout_sec=8)

        # 2) 검색 입력창
        try:
            search_box = WebDriverWait(driver, int(os.getenv("WAIT_SEARCHBOX_SEC", "10"))).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SEL_SEARCH_INPUT))
            )
        except TimeoutException:
            log(f"검색 입력창을 찾지 못함 ({keyword})", "ERROR")
            return False

        try:
            search_box.clear()
        except Exception:
            pass
        search_box.send_keys(keyword)
        search_box.send_keys(Keys.RETURN)

        wait_for_ready(driver, timeout_sec=int(os.getenv("READY_TIMEOUT_SEC", "10")))
        time.sleep(1.0)  # 결과 SPA 렌더링
        return True

    except Exception as e:
        log(f"검색 실패 ({keyword}): {e}", "ERROR")
        return False


def click_first_product(driver):
    """
    검색 결과 첫 상품 클릭. 새 SPA 에서는 카드 자체가 <a href='/product/...'>.
    """
    try:
        cards = WebDriverWait(driver, int(os.getenv("WAIT_PRODUCTS_SEC", "12"))).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, SEL_PRODUCT_CARD))
        )
        if not cards:
            return False
        # stale 대응: 첫 카드 요소를 한 번 더 다시 찾아 클릭 시도
        for attempt in range(int(os.getenv("CARD_CLICK_RETRY", "3"))):
            try:
                fresh = driver.find_elements(By.CSS_SELECTOR, SEL_PRODUCT_CARD)
                if not fresh:
                    time.sleep(0.4)
                    continue
                first = fresh[0]
                if _try_click(driver, first):
                    wait_for_ready(driver, timeout_sec=int(os.getenv("DETAIL_READY_TIMEOUT_SEC", "15")))
                    return True
            except Exception:
                pass
            time.sleep(0.4)
        log("첫 번째 상품 클릭 실패(재시도 한계)", "DEBUG")
        return False
    except TimeoutException:
        return False
    except Exception as e:
        log(f"첫 번째 상품 클릭 실패: {e}", "DEBUG")
        return False


def _find_buy_button(driver):
    # 텍스트 정확일치 우선
    try:
        xpath = (
            f"//button[normalize-space(.)='{SEL_BUY_BUTTON_TEXT}']"
            f" | //a[normalize-space(.)='{SEL_BUY_BUTTON_TEXT}']"
        )
        for el in driver.find_elements(By.XPATH, xpath):
            if el.is_displayed():
                return el
    except Exception:
        pass
    # class 기반 폴백
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, SEL_BUY_BUTTON_CSS):
            if el.is_displayed() and (el.text or "").strip().startswith("구매"):
                return el
    except Exception:
        pass
    return None


def click_buy_and_open_size_list(driver) -> bool:
    """
    sticky '구매하기' 클릭 → Drawer 등장 대기 → 사이즈 콤보박스 클릭으로 listbox 펼침.
    로그인이 안 된 상태에서는 confirm 모달이 떠 옵션 시트가 절대 안 뜨므로 False 반환.
    """
    # 1) sticky CTA 가 React 로 re-render 되면 element 가 stale 되는 경우가 잦다.
    #    polling 과 클릭을 한 루프에 묶어 stale 발생 시 즉시 재발견·재클릭한다.
    deadline = time.time() + int(os.getenv("WAIT_BUY_BUTTON_SEC", "15"))
    clicked = False
    saw_button = False
    while time.time() < deadline:
        btn = _find_buy_button(driver)
        if btn:
            saw_button = True
            try:
                rect = driver.execute_script(
                    "const r=arguments[0].getBoundingClientRect();"
                    "return r.width*r.height;",
                    btn,
                )
            except Exception:
                rect = 0
            if rect and rect > 0:
                if _try_click(driver, btn):
                    clicked = True
                    break
                # 클릭 실패는 보통 stale. 잠시 후 element 재발견하여 다시 시도.
        time.sleep(0.4)

    if not clicked:
        if not saw_button:
            log("구매하기 버튼이 나타나지 않음", "WARNING")
            _dump_failure(driver, "_", "buy_button_missing")
        else:
            log("구매하기 클릭 실패(stale 재시도 한계 초과)", "ERROR")
            _dump_failure(driver, "_", "buy_button_click_failed")
        return False

    # 2) Drawer 등장 대기. 단, 로그인 confirm 모달이 떠 있으면 옵션 시트로 진행 불가.
    try:
        WebDriverWait(driver, int(os.getenv("WAIT_DRAWER_SEC", "8"))).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SEL_OPTION_DRAWER))
        )
    except TimeoutException:
        log("옵션 Drawer 가 등장하지 않음(로그인 필요 가능)", "WARNING")
        _dump_failure(driver, "_", "drawer_missing")
        return False

    # confirm 모달(로그인 필요) 휴리스틱: Drawer 가 떠 있어도 사이즈 콤보박스가 없으면 confirm
    try:
        combobox = WebDriverWait(driver, int(os.getenv("WAIT_COMBOBOX_SEC", "5"))).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SEL_SIZE_COMBOBOX))
        )
    except TimeoutException:
        log("사이즈 콤보박스가 없음(로그인 필요/단일옵션 가능)", "WARNING")
        _dump_failure(driver, "_", "size_combobox_missing")
        return False

    # 3) 콤보박스 클릭으로 listbox 펼침 (이미 펼쳐져 있으면 그대로 둠)
    try:
        expanded = (combobox.get_attribute("aria-expanded") or "").lower() == "true"
    except Exception:
        expanded = False
    if not expanded:
        _try_click(driver, combobox)
    try:
        WebDriverWait(driver, int(os.getenv("WAIT_LISTBOX_SEC", "6"))).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SEL_SIZE_LISTBOX))
        )
    except TimeoutException:
        log("사이즈 listbox 가 펼쳐지지 않음", "WARNING")
        return False
    return True


def parse_size_stocks(layer_html: str) -> list[dict]:
    """
    옵션 Drawer 의 outerHTML 을 받아 [{size, qty, soldout}] 리스트 반환.
    selenium 의존 없이 정규식 기반으로 동작 — 단위 테스트가 쉬움.

    예: <li class="Select_option___q_RU" aria-disabled="false">
          <div class="CtaDrawer_options__rpiQE">
            <div class="CtaDrawer_left___2ULy"><span>S</span>[남은수량 : 10] </div>
            ...
          </div>
        </li>

    품절: <span>XS</span><span>[품절]</span>
    """
    if not layer_html:
        return []

    result: list[dict] = []
    li_iter = re.finditer(
        r"<li\b[^>]*\bclass=\"[^\"]*Select_option___q_RU[^\"]*\"[^>]*?>([\s\S]*?)</li>",
        layer_html,
    )
    for m in li_iter:
        attrs_match = re.search(r"<li\b([^>]*)>", m.group(0))
        attrs = attrs_match.group(1) if attrs_match else ""
        disabled = (
            "aria-disabled=\"true\"" in attrs
            or "Select_disabled" in attrs
        )
        body = m.group(1)
        left_match = re.search(
            r"<div[^>]*CtaDrawer_left[^>]*>([\s\S]*?)</div>", body
        )
        if not left_match:
            continue
        left_html = left_match.group(1)
        spans = re.findall(r"<span[^>]*>([\s\S]*?)</span>", left_html)
        size = (spans[0] if spans else "").strip()
        if not size:
            continue
        text_only = re.sub(r"<[^>]+>", "", left_html)
        qty = None
        soldout = False
        m_qty = _QTY_RE.search(text_only)
        if m_qty:
            try:
                qty = int(m_qty.group(1))
            except Exception:
                qty = None
        if _SOLDOUT_RE.search(text_only) or disabled:
            soldout = True
            if qty is None:
                qty = 0
        result.append({"size": size, "qty": qty, "soldout": soldout})
    return result


def get_size_stocks(driver) -> list[dict]:
    """
    옵션 Drawer 가 펼쳐진 상태에서 사이즈+수량을 수집한다.
    """
    try:
        drawer = driver.find_element(By.CSS_SELECTOR, SEL_OPTION_DRAWER)
        html = drawer.get_attribute("outerHTML") or ""
        return parse_size_stocks(html)
    except Exception as e:
        log(f"사이즈/수량 수집 실패: {e}", "DEBUG")
        return []


def match_size_stocks(stocks: list[dict], target_sizes: list[str]) -> list[dict]:
    """
    타겟 사이즈를 정확 일치(공백/대소문자 무시)로 매칭. soldout=True 또는 qty==0 은 제외.
    반환: [{size, qty, soldout}]
    """
    available_map: dict[str, dict] = {}
    for s in stocks:
        token = normalize_size_token(s.get("size", ""))
        if not token:
            continue
        if s.get("soldout") or (s.get("qty") is not None and s.get("qty") == 0):
            continue
        available_map.setdefault(token, s)

    matched: list[dict] = []
    seen: set[str] = set()
    for target in target_sizes:
        token = normalize_size_token(target)
        if token in available_map and token not in seen:
            entry = available_map[token]
            matched.append({
                "size": target,
                "qty": entry.get("qty"),
                "soldout": False,
            })
            seen.add(token)
    return matched


# 하위 호환: 기존 호출부 / 테스트(list[str] 기준) 가 깨지지 않도록 유지
def get_available_options(driver) -> list[str]:
    return [s["size"] for s in get_size_stocks(driver) if not s.get("soldout")]

# =========================
# 텔레그램 알림 (requests 직접 전송 + 재시도)
# =========================
def _format_size_with_qty(sizes, size_stocks):
    """sizes(list[str])와 size_stocks(list[dict])를 받아 'L (3개), M' 같은 문자열로 변환."""
    if not size_stocks:
        return ", ".join(sizes or [])
    qty_map: dict[str, int] = {}
    for s in size_stocks:
        key = (s.get("size") or "").strip()
        if not key:
            continue
        q = s.get("qty")
        if isinstance(q, int):
            qty_map[key] = q
    parts: list[str] = []
    for sz in sizes or []:
        q = qty_map.get(sz)
        parts.append(f"{sz} ({q}개)" if isinstance(q, int) else sz)
    return ", ".join(parts)


def send_telegram_alert(product, sizes, url, size_stocks=None, ack_link=None):
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

        sizes_line = _format_size_with_qty(sizes, size_stocks)
        msg = f"🔔 재고 알림!\n\n상품: {product}\n사이즈: {sizes_line}\n\n{url}"
        if ack_link:
            msg += f"\n\n▶ 이 알림 그만 받기(ACK): {ack_link}"

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

        # 0) 사전 로그인 (HYUNDAI_LOGIN_ID/PW 가 설정돼 있을 때만).
        #    실패해도 검색은 진행 — 비회원 상태에서는 옵션 시트가 안 떠 OUT_OF_STOCK 으로 분기.
        ensure_logged_in(driver)

        # 1) 검색 (재시도)
        searched = False
        for i in range(step_retries + 1):
            if search_product(driver, keyword):
                searched = True
                break
            # 검색 실패 시 드라이버를 새로 만들어 재시도(세션 꼬임 방지)
            safe_quit_driver(driver)
            driver = create_driver()
            ensure_logged_in(driver)  # 새 driver 라 세션이 비어있으니 재로그인
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

        # 3) 구매하기 클릭 → 옵션 Drawer → 사이즈 listbox 펼침
        opened = click_buy_and_open_size_list(driver)
        if not opened:
            log(f"{keyword} - 옵션 시트 펼침 실패(로그인 필요/단일옵션 가능)", "INFO")
            return {"status": StockStatus.OUT_OF_STOCK.value, "product": keyword}

        # 4) 사이즈+수량 수집
        size_stocks = get_size_stocks(driver)
        if not size_stocks:
            log(f"{keyword} - 옵션 없음", "INFO")
            return {"status": StockStatus.OUT_OF_STOCK.value, "product": keyword}

        all_options_human = [
            f"{s['size']}({'품절' if s.get('soldout') else (s.get('qty') if s.get('qty') is not None else '?')})"
            for s in size_stocks
        ]

        # 5) 사이즈 매칭 (품절 제외)
        matched = match_size_stocks(size_stocks, target_sizes)

        elapsed = time.time() - start_time

        if matched:
            sizes_only = [m["size"] for m in matched]
            human_matched = [
                f"{m['size']}({m['qty']}개)" if m.get("qty") is not None else m["size"]
                for m in matched
            ]
            log(
                f"{keyword} - 재고 확인 성공 | 타겟: [{', '.join(target_sizes)}] | "
                f"재고: [{', '.join(all_options_human)}] | 매칭: [{', '.join(human_matched)}] ({elapsed:.1f}초)",
                "SUCCESS",
            )
            return {
                "status": StockStatus.IN_STOCK.value,
                "product": keyword,
                "sizes": sizes_only,                       # legacy 호환 (list[str])
                "size_stocks": matched,                    # 신규 (list[dict])
                "available_options": [s["size"] for s in size_stocks],
                "available_size_stocks": size_stocks,
                "url": driver.current_url,
            }

        log(
            f"{keyword} - 재고 없음 | 타겟: [{', '.join(target_sizes)}] | "
            f"재고: [{', '.join(all_options_human)}] ({elapsed:.1f}초)",
            "INFO",
        )
        return {
            "status": StockStatus.OUT_OF_STOCK.value,
            "product": keyword,
            "available_options": [s["size"] for s in size_stocks],
            "available_size_stocks": size_stocks,
        }

    except Exception as e:
        elapsed = time.time() - start_time
        log(f"{keyword} - 오류 발생: {e} ({elapsed:.1f}초)", "ERROR")
        if "StockStatus" in str(e):
            log(traceback.format_exc(), "ERROR")
        return {"status": "error", "product": keyword, "error": str(e)}

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

        # 워커 수: HYUNDAI_MAX_WORKERS (기본 1). 메모리/CPU 폭주 방지를 위해 1~4 로 클램프.
        # 안내: Chrome 1 인스턴스당 약 300~500MB. 2 vCPU/4GB 환경에서는 2 가 안전 상한.
        try:
            requested = int(os.getenv("HYUNDAI_MAX_WORKERS", "1"))
        except ValueError:
            requested = 1
        max_workers = max(1, min(requested, 4))
        if not check_system_resources():
            log(f"리소스 체크 경고 — 그대로 진행(max_workers={max_workers})", "WARNING")
        log(f"워커 수: {max_workers} (env HYUNDAI_MAX_WORKERS={os.getenv('HYUNDAI_MAX_WORKERS', '미설정')})", "INFO")

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
                    "size_stocks": r.get("size_stocks", []),
                    "url": r["url"]
                })
            elif status in [StockStatus.SEARCH_FAILED.value, StockStatus.UNKNOWN_ERROR.value, StockStatus.PAGE_ERROR.value]:
                system_errors.append(r)
                alert_policy.record_ops_status(r.get("product", "unknown"), {
                    "last_status": status,
                    "last_message": r.get("error", "crawler error"),
                    "is_error": True,
                })
            elif status == StockStatus.OUT_OF_STOCK.value:
                # 품절 감지 → 해당 상품의 IN_STOCK dedup 상태(ACK/카운터/마지막발송)
                # 를 리셋. 재입고되면 새 이벤트로 알림이 다시 발송된다.
                product = r.get("product", "")
                if product:
                    reset_key = alert_policy.make_dedup_key(
                        product, "ALL", StockStatus.IN_STOCK.value
                    )
                    if alert_policy.clear(reset_key):
                        log(f"{product} - 품절 감지 → 알림 상태 리셋(재입고 시 재알림)", "DEBUG")

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
                size_stocks = item.get("size_stocks", [])
                url = item["url"]

                # 사이즈 + 수량 텍스트 (예: "L (3개), M (1개)")
                sizes_human = _format_size_with_qty(sizes, size_stocks)
                # 이메일/텔레그램에 보낼 문자열 리스트 (사이즈만 vs 사이즈+수량)
                sizes_for_alert = [
                    f"{m['size']} ({m['qty']}개)" if isinstance(m.get("qty"), int) else m["size"]
                    for m in (size_stocks or [{"size": s, "qty": None} for s in sizes])
                ]

                dedup_prefix = alert_policy.make_dedup_key(product, "ALL", StockStatus.IN_STOCK.value)
                policy_mode = os.getenv("ALERT_POLICY_MODE", "v1")
                decision = alert_policy.should_send(dedup_prefix, policy_mode=policy_mode)
                if not decision.should_send:
                    log(f"알림 스킵: {product} ({decision.reason})", "DEBUG")
                    continue

                try:
                    from stock_check.app.services.alert_token import build_ack_link
                    ack_link = build_ack_link("hyundai", dedup_prefix)
                except Exception:
                    ack_link = None

                try:
                    if send_stock_alert("hyundai", product, sizes_for_alert, url,
                                         dedup_prefix=dedup_prefix, ack_link=ack_link):
                        email_sent += 1
                except Exception as e:
                    log(f"이메일 알림 실패: {product} | {e}", "ERROR")

                try:
                    if send_telegram_alert(product, sizes, url,
                                            size_stocks=size_stocks, ack_link=ack_link):
                        telegram_sent += 1
                except Exception as e:
                    log(f"텔레그램 알림 실패: {product} | {e}", "ERROR")

                alert_policy.mark_sent(dedup_prefix, StockStatus.IN_STOCK.value)
                alert_policy.record_ops_status(product, {
                    "last_status": StockStatus.IN_STOCK.value,
                    "last_message": f"sizes={sizes_human}",
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
                "available_items": [
                    {
                        "product": i["product"],
                        "sizes": i["sizes"],
                        "size_stocks": i.get("size_stocks", []),
                    }
                    for i in available_items
                ]
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
        if "AlertPolicy" in str(e) or "StockStatus" in str(e):
            log(traceback.format_exc(), "ERROR")
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
