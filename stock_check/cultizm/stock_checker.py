#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_check/cultizm/stock_checker.py
컬티즘 재고 확인 스크립트 (최소 로그 버전)
"""

import sys
import os
import time
import threading
import json
import builtins
import traceback
from pathlib import Path
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementNotInteractableException, ElementClickInterceptedException
from dotenv import load_dotenv
import concurrent.futures

# 공통 모듈 경로 추가
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))
from stock_check.shared.email_utils import send_stock_alert, send_system_alert

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

# 통합 환경변수 로드
load_dotenv(PROJECT_ROOT / 'stock_check' / 'shared' / '.env')
load_dotenv()

# 설정
DATA_ROOT = Path(os.getenv('STOCK_CHECK_DATA_ROOT', str(PROJECT_ROOT / 'stock_check')))
BASE_DIR = DATA_ROOT / 'cultizm'
TARGET_FILE = BASE_DIR / 'targets.txt'
LOG_DIR = BASE_DIR / 'logs'
LOCK_FILE = BASE_DIR / 'stock_checker.lock'

# 로그 디렉토리 생성
os.makedirs(LOG_DIR, exist_ok=True)

# 스레드 로컬 스토리지
thread_local = threading.local()

# 스레드 안전 로그
log_lock = threading.Lock()

def get_log_file():
    """날짜별 로그 파일 경로 반환"""
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"log-{today}.txt")

def log(message, level="INFO"):
    """직관적이고 구조화된 로그 기록"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    thread_id = threading.current_thread().ident
    
    # 레벨별 이모지 및 색상 구분
    level_icons = {
        "INFO": "ℹ️",
        "SUCCESS": "✅",
        "ERROR": "❌",
        "WARNING": "⚠️",
        "DEBUG": "🔍"
    }
    icon = level_icons.get(level, "•")
    
    log_message = f"[{timestamp}] {icon} [{level:7s}] {message}"
    
    # 디버그 모드 확인
    debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"
    
    with log_lock:
        # 디버그 모드일 때는 모든 로그 출력, 아니면 SUCCESS/ERROR/WARNING만
        if debug_mode:
            print(log_message)
        elif level in ["SUCCESS", "ERROR", "WARNING"]:
            print(log_message)
        
        try:
            log_file = get_log_file()
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_message + "\n")
        except:
            pass

# 중복 실행 방지
def create_lock():
    try:
        if os.path.exists(LOCK_FILE):
            lock_age = time.time() - os.path.getmtime(LOCK_FILE)
            if lock_age > 1800:
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

def check_system_resources():
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        memory_percent = psutil.virtual_memory().percent
        
        log(f"시스템 리소스 - CPU: {cpu_percent:.1f}%, 메모리: {memory_percent:.1f}%", "INFO")
        
        if cpu_percent > 80 or memory_percent > 80:
            log(f"시스템 리소스 부족", "WARNING")
            return False
        
        return True
    except ImportError:
        return True
    except Exception as e:
        log(f"리소스 체크 실패: {e}", "INFO")
        return True

# 크롬 드라이버 설정
def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-images")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--memory-pressure-off")
    chrome_options.add_argument("--max_old_space_size=512")
    chrome_options.add_argument("--window-size=1280,720")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver_path = os.getenv("CHROME_DRIVER_PATH", "/usr/local/bin/chromedriver")
    service = ChromeService(executable_path=driver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    return driver

def safe_click(driver, element, max_attempts=2):
    for attempt in range(max_attempts):
        try:
            timeout = int(os.getenv("ELEMENT_WAIT_TIMEOUT", "5"))
            WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(element))
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
            time.sleep(0.2)
            driver.execute_script("arguments[0].click();", element)
            return True
        except (ElementNotInteractableException, ElementClickInterceptedException):
            if attempt < max_attempts - 1:
                time.sleep(0.5)
        except:
            pass
    return False

def wait_for_page_load(driver, timeout=None):
    if timeout is None:
        timeout = int(os.getenv("PAGE_LOAD_TIMEOUT", "8"))
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(0.5)
    except:
        pass

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
                
                targets.append({
                    "keyword": keyword.strip(),
                    "sizes": sizes,
                    "line_num": line_num
                })
        
        return targets
    except Exception as e:
        log(f"targets.txt 로드 실패: {e}", "ERROR")
        return []

def handle_cookies(driver):
    try:
        cookie_selectors = [
            "[id*='cookie'], [class*='cookie']",
            "[id*='accept'], [class*='accept']"
        ]
        
        for selector in cookie_selectors:
            try:
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                for btn in buttons:
                    if btn.is_displayed():
                        btn.click()
                        return
            except:
                continue
    except:
        pass

def click_search_trigger(driver):
    trigger_selectors = [
        "a[aria-controls='search-drawer']",
        "a.tap-area[aria-controls='search-drawer']",
        "[data-search='true']",
        ".icon-search"
    ]
    
    for selector in trigger_selectors:
        try:
            triggers = driver.find_elements(By.CSS_SELECTOR, selector)
            for trigger in triggers:
                if trigger.is_displayed():
                    if safe_click(driver, trigger):
                        time.sleep(0.5)
                        return True
        except:
            continue
    
    return False

def try_predictive_search_click(driver, keyword):
    """자동완성 검색 결과에서 첫 번째 제품 클릭 시도"""
    try:
        log(f"자동완성 결과 확인 중...", "DEBUG")
        
        # 자동완성 결과 컨테이너 찾기
        predictive_selectors = [
            ".predictive-search__results-list a[href*='/products/']",
            ".predictive-search__results a[href*='/products/']",
            ".predictive-search a[href*='/products/']",
            "[id*='predictive-search'] a[href*='/products/']",
            ".search-drawer__results a[href*='/products/']",
            ".predictive-search__item a[href*='/products/']",
            "predictive-search-results a[href*='/products/']"
        ]
        
        for selector in predictive_selectors:
            try:
                results = driver.find_elements(By.CSS_SELECTOR, selector)
                if not results:
                    continue
                    
                log(f"자동완성 결과: {len(results)}개 제품 발견", "DEBUG")
                
                # 표시되는 첫 번째 제품 링크 찾기
                for idx, result in enumerate(results):
                    try:
                        if not result.is_displayed():
                            continue
                            
                        href = result.get_attribute('href')
                        if not href or '/products/' not in href:
                            continue
                        
                        # 제품명 가져오기
                        try:
                            product_name = result.text.strip()
                        except:
                            product_name = ""
                        
                        if product_name:
                            log(f"자동완성 첫 번째 제품: {product_name[:80]}", "DEBUG")
                        
                        log(f"자동완성 제품 클릭: {href}", "DEBUG")
                        driver.get(href)
                        return True
                        
                    except:
                        continue
                    
            except:
                continue
        
        log(f"자동완성 결과에서 제품을 찾지 못함", "DEBUG")
        return False
        
    except Exception as e:
        log(f"자동완성 클릭 오류: {e}", "DEBUG")
        return False

def search_keyword(driver, keyword):
    search_selectors = [
        "input[name='q']",
        "input[type='search'][name='q']",
        ".main-search--field",
        "input[type='search']"
    ]
    
    search_box = None
    for selector in search_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed() or element.is_enabled():
                    search_box = element
                    break
            if search_box:
                break
        except:
            continue
    
    if not search_box:
        try:
            search_url = f"https://www.cultizm.com/kor/search?sSearch={keyword.replace(' ', '+')}"
            driver.get(search_url)
            return True
        except:
            return False
    
    try:
        try:
            driver.execute_script("""
                arguments[0].style.display = 'block';
                arguments[0].style.visibility = 'visible';
                arguments[0].style.opacity = '1';
            """, search_box)
        except:
            pass
        
        # 검색창 포커스
        driver.execute_script("arguments[0].focus();", search_box)
        search_box.clear()
        time.sleep(0.2)
        
        # 검색어 한 번에 입력 (복사 붙여넣기처럼)
        log(f"검색어 입력: {keyword}", "DEBUG")
        driver.execute_script("arguments[0].value = arguments[1];", search_box, keyword)
        
        # 입력 이벤트 트리거 (자동완성 활성화)
        driver.execute_script("""
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            arguments[0].dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
        """, search_box)
        
        log(f"자동완성 로드 대기 중...", "DEBUG")
        # 자동완성 결과가 완전히 로드되도록 충분히 대기
        time.sleep(2.5)
        
        # 자동완성 결과 클릭 시도
        if try_predictive_search_click(driver, keyword):
            return True
        
        # 자동완성 실패 시 기존 로직: submit
        log(f"자동완성 실패, submit으로 검색 진행", "DEBUG")
        try:
            search_box.send_keys(Keys.RETURN)
            time.sleep(1)
            return True
        except:
            pass
        
        search_button_selectors = [
            "button[type='submit'].btn.grid",
            "button.close-button",
            "form[action='/search'] button[type='submit']"
        ]
        
        button_clicked = False
        for selector in search_button_selectors:
            try:
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                for btn in buttons:
                    if btn.is_displayed():
                        if safe_click(driver, btn):
                            button_clicked = True
                            break
                if button_clicked:
                    break
            except:
                continue
        
        if not button_clicked:
            driver.execute_script("""
                var form = arguments[0].closest('form');
                if (form) {
                    form.submit();
                }
            """, search_box)
        
        return True
        
    except:
        try:
            search_url = f"https://www.cultizm.com/kor/search?sSearch={keyword.replace(' ', '+')}"
            driver.get(search_url)
            return True
        except:
            return False

def click_first_product(driver, keyword):
    """검색 결과에서 첫 번째 제품 클릭"""
    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".product-list, product-list"))
        )
        time.sleep(1)
    except:
        pass
    
    product_selectors = [
        "product-card a",
        ".product-card a",
        "product-card",
        ".product-card",
        ".product--image",
        ".product-item",
        ".product a"
    ]
    
    log(f"검색 결과 페이지에서 첫 번째 제품 찾는 중...", "DEBUG")
    
    for selector in product_selectors:
        try:
            timeout = int(os.getenv("ELEMENT_WAIT_TIMEOUT", "5"))
            products = WebDriverWait(driver, timeout).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, selector))
            )
            
            if not products:
                continue
            
            log(f"검색 결과: {len(products)}개 제품 발견", "DEBUG")
            
            first_product = products[0]
            
            # 제품명 출력
            try:
                product_title = first_product.text.strip()
                if product_title:
                    log(f"첫 번째 제품: {product_title[:80]}", "DEBUG")
            except:
                pass
            
            # 클릭 시도
            if first_product.tag_name.lower() == 'a':
                href = first_product.get_attribute('href')
                if href:
                    driver.get(href)
                    return True
            
            try:
                product_link = first_product.find_element(By.CSS_SELECTOR, "a")
                href = product_link.get_attribute('href')
                if href:
                    driver.get(href)
                    return True
            except:
                pass
            
            if safe_click(driver, first_product):
                return True
            
            break
        except TimeoutException:
            continue
        except:
            continue
    
    return False

def verify_product_match(driver, keyword):
    """제품 페이지의 제품명이 검색어와 매칭되는지 확인"""
    try:
        # 제품명 가져오기 시도
        title_selectors = [
            "h1.product-title",
            "h1.product--title",
            ".product-title h1",
            ".product--title h1",
            "h1[itemprop='name']",
            ".product-info h1",
            "h1"
        ]
        
        product_title = ""
        for selector in title_selectors:
            try:
                element = driver.find_element(By.CSS_SELECTOR, selector)
                product_title = element.text.strip()
                if product_title:
                    break
            except:
                continue
        
        if not product_title:
            log(f"제품명을 찾을 수 없음, 허용", "DEBUG")
            return True
        
        log(f"제품 페이지 제품명: {product_title}", "DEBUG")
        
        # 검색 키워드의 핵심 단어 추출 (3글자 이상)
        keyword_parts = [part for part in keyword.lower().split() if len(part) >= 3]
        product_title_lower = product_title.lower()
        
        # URL도 확인
        current_url = driver.current_url.lower()
        log(f"현재 URL: {current_url}", "DEBUG")
        
        # 브랜드명(첫 번째 단어)은 필수로 매칭되어야 함
        if keyword_parts:
            brand_name = keyword_parts[0]
            brand_in_title = brand_name in product_title_lower
            brand_in_url = brand_name in current_url
            
            log(f"브랜드명 '{brand_name}' 체크 - 제품명: {brand_in_title}, URL: {brand_in_url}", "DEBUG")
            
            if not brand_in_title and not brand_in_url:
                log(f"브랜드명 불일치 - 검색어: {keyword}, 제품: {product_title}", "WARNING")
                return False
        
        # 전체 키워드 매칭 개수 계산
        matched_count = sum(1 for part in keyword_parts if part in product_title_lower or part in current_url)
        total_count = len(keyword_parts)
        
        log(f"키워드 매칭: {matched_count}/{total_count} ({keyword_parts})", "DEBUG")
        
        # 브랜드명 + 최소 1개 이상 추가 매칭되면 OK
        if matched_count >= 2:
            log(f"제품명 매칭 성공 (매칭 키워드 {matched_count}개)", "DEBUG")
            return True
        else:
            log(f"제품명 매칭 실패 - 검색어: {keyword}, 제품: {product_title}", "WARNING")
            return False
        
    except Exception as e:
        log(f"제품명 검증 오류: {e}, 허용", "DEBUG")
        return True

def get_available_sizes(driver):
    try:
        swatch_labels = driver.find_elements(By.CSS_SELECTOR, "label.block-swatch")
        
        if swatch_labels:
            available_sizes = []
            for label in swatch_labels:
                try:
                    label_classes = label.get_attribute('class') or ''
                    if 'is-disabled' not in label_classes:
                        size_span = label.find_element(By.CSS_SELECTOR, "span")
                        size_text = size_span.text.strip()
                        if size_text:
                            available_sizes.append(size_text)
                except:
                    continue
            
            if available_sizes:
                return available_sizes
        
        # fallback
        size_container_selectors = [
            ".product--configurator .field--select",
            ".size-selector"
        ]
        
        size_container = None
        for selector in size_container_selectors:
            try:
                containers = driver.find_elements(By.CSS_SELECTOR, selector)
                for container in containers:
                    if container.is_displayed():
                        size_container = container
                        break
                if size_container:
                    break
            except:
                continue
        
        if size_container:
            safe_click(driver, size_container)
            time.sleep(0.5)
        
        option_selectors = [
            ".product--configurator .field--select option",
            ".size-selector option"
        ]
        
        option_elements = []
        for selector in option_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                visible_elements = [e for e in elements if e.is_displayed()]
                if visible_elements:
                    option_elements = visible_elements
                    break
            except:
                continue
        
        if not option_elements:
            return []
        
        available_sizes = []
        unavailable_keywords = [
            '선택 불가', '선택불가', '품절', 'sold out', 'out of stock',
            'unavailable', 'disabled', 'not available', '재고없음', '없음'
        ]
        
        for opt in option_elements:
            try:
                text = opt.get_attribute('textContent') or opt.text or opt.get_attribute('value')
                if text and text.strip():
                    text = text.strip()
                    if text.lower() not in ['choose', 'select', '선택', '선택해주세요']:
                        is_unavailable = any(keyword in text.lower() for keyword in unavailable_keywords)
                        if not is_unavailable:
                            available_sizes.append(text)
            except:
                continue
        
        return available_sizes
        
    except:
        return []

def send_telegram_alert(product, sizes, url):
    try:
        import subprocess
        
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        if not bot_token or not chat_id:
            return False
        
        # 텔레그램 발송 이력 확인
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
        
        # 현재 시각
        current_time = time.time()
        
        # 같은 상품의 최근 발송 이력 확인
        for record in history:
            if (record.get("product") == product and 
                record.get("site") == "cultizm"):
                last_sent = record.get("timestamp", 0)
                elapsed = current_time - last_sent
                
                if elapsed < telegram_interval:
                    remaining = telegram_interval - elapsed
                    log(f"텔레그램 중복 방지: {product} (남은 시간: {remaining/60:.1f}분)", "DEBUG")
                    return False
        
        # 텔레그램 발송
        telegram_script = os.path.join(BASE_DIR, "telegram_sender.py")
        
        if not os.path.exists(telegram_script):
            create_telegram_script(telegram_script)
        
        subprocess.Popen(
            [
                "python3",
                telegram_script,
                bot_token,
                chat_id,
                product,
                ",".join(sizes),
                url
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        
        # 발송 이력 저장
        history.append({
            "site": "cultizm",
            "product": product,
            "sizes": sizes,
            "timestamp": current_time,
            "datetime": datetime.now().isoformat()
        })
        
        # 오래된 이력 삭제 (7일 이상)
        week_ago = current_time - (7 * 24 * 3600)
        history = [h for h in history if h.get("timestamp", 0) > week_ago]
        
        try:
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except:
            pass
        
        return True
        
    except:
        return False

def create_telegram_script(script_path):
    script_content = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import time
import os
from dotenv import load_dotenv
from pathlib import Path

env_file = os.getenv('STOCK_CHECK_ENV_FILE')
if env_file:
    load_dotenv(env_file)
load_dotenv()

def send_telegram_message(bot_token, chat_id, message, repeat_count, interval):
    try:
        import requests
        
        for i in range(repeat_count):
            try:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "disable_web_page_preview": False
                    },
                    timeout=5
                )
            except:
                pass
            
            if i < repeat_count - 1:
                time.sleep(interval)
    except:
        pass

if __name__ == "__main__":
    if len(sys.argv) < 6:
        sys.exit(1)
    
    bot_token = sys.argv[1]
    chat_id = sys.argv[2]
    product = sys.argv[3]
    sizes = sys.argv[4]
    url = sys.argv[5]
    
    repeat_count = int(os.getenv("TELEGRAM_REPEAT_COUNT", "5"))
    interval = float(os.getenv("TELEGRAM_INTERVAL", "3.0"))
    
    message = f"🔔 재고 알림!\\n\\n상품: {product}\\n사이즈: {sizes}\\n\\n{url}"
    
    send_telegram_message(bot_token, chat_id, message, repeat_count, interval)
'''
    
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        os.chmod(script_path, 0o755)
    except:
        pass

def check_single_stock(target):
    keyword = target["keyword"]
    target_sizes = target["sizes"]
    
    driver = None
    start_time = time.time()
    
    try:
        driver = create_driver()
        
        driver.get("https://www.cultizm.com/kor/")
        wait_for_page_load(driver)
        
        handle_cookies(driver)
        click_search_trigger(driver)
        
        if not search_keyword(driver, keyword):
            log(f"{keyword} - 검색 실패", "ERROR")
            return {"status": StockStatus.SEARCH_FAILED.value, "product": keyword}
        
        wait_for_page_load(driver)
        
        # 자동완성에서 제품을 찾지 못한 경우 결과 페이지에서 찾기
        current_url = driver.current_url
        if '/products/' not in current_url:
            if not click_first_product(driver, keyword):
                log(f"{keyword} - 검색 결과 없음", "INFO")
                return {"status": StockStatus.PRODUCT_NOT_FOUND.value, "product": keyword}
            wait_for_page_load(driver)
        
        # 제품 페이지에서 제품명 검증
        if not verify_product_match(driver, keyword):
            log(f"{keyword} - 검색어와 제품명이 일치하지 않음, 검색 결과 페이지로 재시도", "INFO")
            
            # 검색 결과 페이지로 이동
            search_url = f"https://www.cultizm.com/kor/search?sSearch={keyword.replace(' ', '+')}"
            driver.get(search_url)
            wait_for_page_load(driver)
            
            # 검색 결과 페이지에서 다시 찾기
            if not click_first_product(driver, keyword):
                log(f"{keyword} - 재시도 후에도 검색 결과 없음", "INFO")
                return {"status": StockStatus.PRODUCT_NOT_FOUND.value, "product": keyword}
            
            wait_for_page_load(driver)
            
            # 재검증
            if not verify_product_match(driver, keyword):
                log(f"{keyword} - 재시도 후에도 제품명 불일치", "WARNING")
                return {"status": StockStatus.PRODUCT_NOT_FOUND.value, "product": keyword}
        
        available_sizes = get_available_sizes(driver)
        
        if not available_sizes:
            log(f"{keyword} - 사이즈 옵션 없음", "INFO")
            return {"status": StockStatus.OUT_OF_STOCK.value, "product": keyword}
        
        # 사이즈 매칭
        matched_sizes = []
        for available in available_sizes:
            available_clean = available.replace(" ", "").lower()
            
            for target in target_sizes:
                target_clean = target.replace(" ", "").lower()
                
                if target_clean == available_clean:
                    matched_sizes.append(available)
                    break
        
        elapsed = time.time() - start_time
        
        # 핵심 로그: 검색어, 타겟 사이즈, 재고 있는 사이즈, 매칭 여부
        if matched_sizes:
            log(f"{keyword} - 재고 확인 성공 | 타겟: [{', '.join(target_sizes)}] | 재고: [{', '.join(available_sizes)}] | 매칭: [{', '.join(matched_sizes)}] ({elapsed:.1f}초)", "SUCCESS")
            return {
                "status": StockStatus.IN_STOCK.value,
                "product": keyword,
                "sizes": matched_sizes,
                "url": driver.current_url
            }
        else:
            log(f"{keyword} - 재고 없음 | 타겟: [{', '.join(target_sizes)}] | 재고: [{', '.join(available_sizes)}] ({elapsed:.1f}초)", "INFO")
            return {"status": StockStatus.OUT_OF_STOCK.value, "product": keyword, "available_sizes": available_sizes}
        
    except Exception as e:
        elapsed = time.time() - start_time
        log(f"{keyword} - 오류 발생: {e} ({elapsed:.1f}초)", "ERROR")
        if "StockStatus" in str(e):
            log(traceback.format_exc(), "ERROR")
        return {"status": "error", "product": keyword, "error": str(e)}
    finally:
        if driver:
            try:
                driver.quit()
            except:
                try:
                    driver.service.process.kill()
                except:
                    pass

def main():
    if not create_lock():
        log("이미 실행 중입니다", "WARNING")
        return
    
    try:
        start_time = time.time()
        log("=" * 60, "INFO")
        log("컬티즘 재고 확인 시작", "INFO")
        log("=" * 60, "INFO")
        
        # 타겟 로드
        targets = load_targets()
        if not targets:
            log("검색 대상이 없습니다", "ERROR")
            return
        
        log(f"검색 타겟: {len(targets)}개", "INFO")

        # 워커 수: CULTIZM_MAX_WORKERS (기본 1). 1~4 클램프.
        try:
            requested = int(os.getenv("CULTIZM_MAX_WORKERS", "1"))
        except ValueError:
            requested = 1
        max_workers = max(1, min(requested, 4))
        if not check_system_resources():
            log(f"리소스 체크 경고 — 그대로 진행(max_workers={max_workers})", "WARNING")
        log(f"워커 수: {max_workers} (env CULTIZM_MAX_WORKERS={os.getenv('CULTIZM_MAX_WORKERS', '미설정')})", "INFO")
        
        # 재고 확인 실행
        search_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_target = {executor.submit(check_single_stock, target): target for target in targets}
            
            for future in concurrent.futures.as_completed(future_to_target, timeout=300):
                target = future_to_target[future]
                try:
                    result = future.result(timeout=60)
                    if result:
                        search_results.append(result)
                except concurrent.futures.TimeoutError:
                    log(f"타임아웃: {target['keyword']}", "ERROR")
                    search_results.append({"status": StockStatus.PAGE_ERROR.value, "product": target['keyword']})
                except Exception as e:
                    log(f"Future 오류: {e}", "ERROR")
                    search_results.append({"status": StockStatus.UNKNOWN_ERROR.value, "product": target['keyword'], "error": str(e)})
        
        alert_policy = AlertPolicy("cultizm")

        # 결과 분석
        available_items = []
        system_errors = []
        verification_failed_items = []
        
        for result in search_results:
            status = result.get("status", "unknown")
            
            if status == StockStatus.IN_STOCK.value:
                available_items.append({
                    "product": result["product"],
                    "sizes": result["sizes"], 
                    "url": result["url"]
                })
            elif status == "verification_failed":
                verification_failed_items.append(result)
                log(f"검증 실패로 알림 발송 제외: {result.get('product')} - {result.get('url')}", "WARNING")
            elif status in [StockStatus.SEARCH_FAILED.value, StockStatus.UNKNOWN_ERROR.value, StockStatus.PAGE_ERROR.value]:
                system_errors.append(result)
                alert_policy.record_ops_status(result.get("product", "unknown"), {
                    "last_status": status,
                    "last_message": result.get("error", "crawler error"),
                    "is_error": True,
                })
            elif status == StockStatus.OUT_OF_STOCK.value:
                # 품절 감지 → 재입고 시 재알림되도록 dedup 상태 리셋
                product = result.get("product", "")
                if product:
                    reset_key = alert_policy.make_dedup_key(
                        product, "ALL", StockStatus.IN_STOCK.value
                    )
                    if alert_policy.clear(reset_key):
                        log(f"{product} - 품절 감지 → 알림 상태 리셋(재입고 시 재알림)", "DEBUG")
        
        elapsed_time = time.time() - start_time
        
        # 높은 오류율 체크
        error_count = len(system_errors)
        total_count = len(search_results)
        error_rate = error_count / total_count * 100 if total_count > 0 else 0
        
        # 검증 실패 항목도 로그에 기록
        if verification_failed_items:
            log(f"검증 실패 항목 {len(verification_failed_items)}개 (브랜드 불일치)", "WARNING")
        
        if error_rate >= 50 and total_count >= 3:
            send_system_alert(
                "cultizm", "high_error_rate",
                f"높은 오류율 ({error_rate:.1f}%)",
                f"전체: {total_count}개\n오류: {error_count}개\n성공: {len(available_items)}개"
            )
        
        # 알림 발송
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

                if send_stock_alert("cultizm", product, sizes, url, dedup_prefix=dedup_prefix):
                    email_sent += 1

                if send_telegram_alert(product, sizes, url):
                    telegram_sent += 1

                alert_policy.mark_sent(dedup_prefix, StockStatus.IN_STOCK.value)
                alert_policy.record_ops_status(product, {
                    "last_status": StockStatus.IN_STOCK.value,
                    "last_message": f"sizes={','.join(sizes)}",
                    "product_url": url,
                    "is_error": False,
                })
            
            log(f"재고 확인 완료 | 재고: {len(available_items)}개 | 이메일: {email_sent}개 | 텔레그램: {telegram_sent}개 | 소요시간: {elapsed_time:.1f}초", "SUCCESS")
            
            # 결과 저장
            result = {
                "timestamp": datetime.now().isoformat(),
                "execution_time": f"{elapsed_time:.1f}s",
                "total_checked": len(targets),
                "available_count": len(available_items),
                "email_sent": email_sent,
                "telegram_sent": telegram_sent,
                "threads_used": max_workers,
                "available_items": [{"product": item["product"], "sizes": item["sizes"]} for item in available_items]
            }
            
            results_file = os.path.join(BASE_DIR, "recent_results.json")
            try:
                if os.path.exists(results_file):
                    with open(results_file, "r", encoding="utf-8") as f:
                        recent_results = json.load(f)
                else:
                    recent_results = []
                
                recent_results.insert(0, result)
                recent_results = recent_results[:5]
                
                with open(results_file, "w", encoding="utf-8") as f:
                    json.dump(recent_results, f, indent=2, ensure_ascii=False)
                    
            except:
                pass
        else:
            log(f"재고 확인 완료 | 재고 없음 | 검색: {len(targets)}개 | 오류: {error_count}개 | 검증실패: {len(verification_failed_items)}개 | 소요시간: {elapsed_time:.1f}초", "INFO")
     
    except concurrent.futures.TimeoutError:
        log("전체 프로세스 타임아웃 (5분 초과)", "ERROR")
    except Exception as e:
        log(f"전체 프로세스 오류: {e}", "ERROR")
        if "AlertPolicy" in str(e) or "StockStatus" in str(e):
            log(traceback.format_exc(), "ERROR")
    
    finally:
        remove_lock()
        elapsed_time = time.time() - start_time
        log("=" * 60, "INFO")
        log(f"컬티즘 재고 확인 완료 (소요시간: {elapsed_time:.1f}초)", "INFO")
        log("=" * 60, "INFO")

if __name__ == "__main__":
    main()
