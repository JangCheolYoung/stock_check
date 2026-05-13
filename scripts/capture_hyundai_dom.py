#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/capture_hyundai_dom.py

리뉴얼된 thehyundai.com 의 DOM/스크린샷을 1회 캡쳐해
새 셀렉터를 결정하기 위한 보조 스크립트.

- 검색 → 첫 상품 클릭 → 상세 → "구매하기" 후보 버튼 dump →
  버튼 클릭 시도 → 옵션 레이어 dump → 단계별 스크린샷.
- WebDriver 생성/종료 로직은 기존 stock_checker 모듈을 그대로 재사용한다
  (헬스/타임아웃/UA/headless 옵션 동일).
- 실패해도 가능한 한 산출물을 남기는 best-effort 흐름.

사용 예:
  python scripts/capture_hyundai_dom.py --keyword "MNRROTW16020085001"
  python scripts/capture_hyundai_dom.py --keyword "RRL 콩그레스" --login \
      --out /tmp/hyundai_capture/manual_run

환경변수:
  CHROME_HEADLESS_MODE  : "off"  로 두면 화면을 띄워서 캡쳐(원격에서는 보통 new 유지)
  HYUNDAI_LOGIN_ID/PW   : --login 옵션과 함께 사용
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from stock_check.hyundai.stock_checker import (
    HYUNDAI_HOME as LEGACY_HYUNDAI_HOME,
    create_driver,
    safe_quit_driver,
    wait_for_ready,
)


# 리뉴얼된 더현대Hi(Next.js SPA) 기본 진입 URL. --entry-url 로 덮어쓸 수 있다.
DEFAULT_ENTRY_URL = "https://hi.thehyundai.com/shop/main"

BUY_BUTTON_HINT_TEXTS = ["구매하기", "바로구매", "BUY NOW", "구매 하기", "구매"]
OPTION_LAYER_HINT_SELECTORS = [
    "ul.opt-select-layer",
    ".opt-select-layer",
    ".option-layer",
    ".product-option-layer",
    "[class*='opt-select']",
    "[class*='option-layer']",
    "[class*='OptionLayer']",
    "[class*='OptionSheet']",
    "[role='dialog']",
    "[aria-modal='true']",
    "[data-state='open']",
    "[class*='BottomSheet']",
    "[class*='Bottom_sheet']",
    "[class*='Drawer']",
    "[class*='Sheet']",
    "[class*='Modal']",
    "body > div[id^='radix-']",
    "body > div[class*='portal']",
    "body > div[id^='headlessui-']",
]


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")
    log(f"  → saved {path}")


def save_screenshot(driver, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(path))
        log(f"  → saved {path}")
    except Exception as exc:
        log(f"  ! screenshot 실패: {exc}")


def dump_page(driver, out_dir: Path, name: str) -> None:
    try:
        save_text(out_dir / f"{name}.html", driver.page_source)
    except Exception as exc:
        log(f"  ! page_source 실패: {exc}")
    save_screenshot(driver, out_dir / f"{name}.png")
    try:
        save_text(out_dir / f"{name}.url.txt", driver.current_url)
    except Exception:
        pass


def try_login(driver, out_dir: Path) -> bool:
    user = os.getenv("HYUNDAI_LOGIN_ID")
    pw = os.getenv("HYUNDAI_LOGIN_PW")
    if not user or not pw:
        log("로그인 자격증명 없음(HYUNDAI_LOGIN_ID/PW) → 스킵")
        return False

    log("로그인 시도")
    try:
        driver.get("https://www.thehyundai.com/front/member/login.thd")
        wait_for_ready(driver, timeout_sec=8)
        dump_page(driver, out_dir, "login_page")

        candidates_id = ["#loginId", "input[name='loginId']", "input[name='userId']", "input[type='text']"]
        candidates_pw = ["#loginPwd", "input[name='loginPwd']", "input[name='password']", "input[type='password']"]
        candidates_btn = [
            "button.btn-login",
            "a.btn-login",
            "button[type='submit']",
            "input[type='submit']",
        ]

        def first(selectors):
            for sel in selectors:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    if el.is_displayed():
                        return el
                except Exception:
                    continue
            return None

        id_el = first(candidates_id)
        pw_el = first(candidates_pw)
        if not id_el or not pw_el:
            log("  ! 로그인 입력 요소를 못 찾음 (로그인 페이지 셀렉터는 캡쳐 결과 보고 보강 필요)")
            return False

        id_el.clear(); id_el.send_keys(user)
        pw_el.clear(); pw_el.send_keys(pw)

        btn_el = first(candidates_btn)
        if btn_el:
            driver.execute_script("arguments[0].click();", btn_el)
        else:
            pw_el.send_keys(Keys.RETURN)

        wait_for_ready(driver, timeout_sec=8)
        dump_page(driver, out_dir, "login_after")
        log("로그인 후 상태 캡쳐 완료(성공 여부는 HTML 확인)")
        return True
    except Exception as exc:
        log(f"  ! 로그인 흐름 예외: {exc}")
        save_text(out_dir / "login_error.txt", traceback.format_exc())
        return False


def _find_visible(driver, locators, timeout: float = 4.0):
    """locators=[(By, selector), ...] 중 처음으로 표시되는 요소를 반환."""
    for by, sel in locators:
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, sel))
            )
            if el and el.is_displayed():
                return el, (by, sel)
        except Exception:
            continue
    return None, None


def _click_search_icon(driver) -> bool:
    """리뉴얼된 hi.thehyundai.com 헤더의 '검색' IconButton 을 클릭한다."""
    locators = [
        (By.CSS_SELECTOR, "button[aria-label='검색']"),
        (By.CSS_SELECTOR, "a[aria-label='검색']"),
        (By.CSS_SELECTOR, "header [aria-label='검색']"),
        (By.CSS_SELECTOR, "[class*='Icon_search']"),
        (By.XPATH, "//button[contains(@aria-label,'검색')]"),
    ]
    el, hit = _find_visible(driver, locators, timeout=3.0)
    if not el:
        return False
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'instant',block:'center'});", el
        )
        driver.execute_script("arguments[0].click();", el)
        log(f"  검색 아이콘 클릭 OK ({hit})")
        return True
    except Exception:
        return False


def do_search(driver, entry_url: str, keyword: str, out_dir: Path) -> bool:
    log(f"검색: {keyword} (entry={entry_url})")
    try:
        driver.get(entry_url)
        wait_for_ready(driver, timeout_sec=10)
        # SPA 렌더링 대기
        time.sleep(1.0)
        dump_page(driver, out_dir, "01_home")

        input_candidates = [
            (By.ID, "cs-token-input"),
            (By.CSS_SELECTOR, "input[type='search']"),
            (By.CSS_SELECTOR, "input[placeholder*='검색']"),
            (By.CSS_SELECTOR, "input[name='searchTerm']"),
            (By.CSS_SELECTOR, "input[name*='search']"),
            (By.CSS_SELECTOR, "input[id*='search']"),
            (By.CSS_SELECTOR, "input.search-input"),
            (By.CSS_SELECTOR, "[data-testid*='search'] input"),
            (By.CSS_SELECTOR, "[class*='SearchBar'] input"),
            (By.CSS_SELECTOR, "[class*='Search_'] input"),
        ]

        # 1) 곧장 input 이 떠 있으면 사용
        box, hit = _find_visible(driver, input_candidates, timeout=3.0)

        # 2) 없으면 검색 아이콘 버튼 클릭 후 다시 탐색
        if not box:
            log("  곧바로 보이는 검색 input 없음 → 검색 아이콘 클릭 시도")
            if _click_search_icon(driver):
                time.sleep(1.0)
                wait_for_ready(driver, timeout_sec=6)
                dump_page(driver, out_dir, "01b_after_search_icon")
                box, hit = _find_visible(driver, input_candidates, timeout=6.0)

        if not box:
            log("  ! 검색창을 못 찾음 - 01_home.html / 01b_after_search_icon.html 확인 필요")
            return False

        log(f"  검색창 발견: {hit}")
        try:
            box.clear()
        except Exception:
            pass
        box.send_keys(keyword)
        box.send_keys(Keys.RETURN)
        wait_for_ready(driver, timeout_sec=10)
        time.sleep(1.2)  # 결과 SPA 렌더링 대기
        dump_page(driver, out_dir, "02_search_result")
        return True
    except Exception as exc:
        log(f"  ! 검색 실패: {exc}")
        save_text(out_dir / "search_error.txt", traceback.format_exc())
        return False


def open_first_product(driver, out_dir: Path) -> bool:
    log("첫 상품 클릭")
    candidates_card = [
        ".prod-unit",
        ".product-card",
        ".product-unit",
        "li.product",
        "[class*='product-item']",
        "[class*='ProductCard']",
        "[class*='ProductItem']",
        "[class*='GoodsCard']",
        "[class*='GoodsItem']",
        "a[href*='/shop/goods']",
        "a[href*='/goods']",
        "a[href*='/product']",
    ]

    found = []
    for sel in candidates_card:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                found.append((sel, len(els)))
        except Exception:
            continue
    log(f"  카드 후보 셀렉터 매치: {found}")
    save_text(out_dir / "02_search_result.candidates.txt", "\n".join(f"{s}\t{n}" for s, n in found))

    first_link = None
    for sel in candidates_card:
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if not cards:
                continue
            first_card = cards[0]

            # 1) 카드 자체가 <a href> 이면 그대로 사용
            try:
                if first_card.tag_name.lower() == "a" and first_card.get_attribute("href"):
                    first_link = first_card
                    log(f"  카드={sel} (a 태그 자체 사용) href={first_link.get_attribute('href')}")
                    break
            except Exception:
                pass

            # 2) 자식에서 링크 탐색
            for link_sel in ("a.title.ellipsis", "a.title", "a[href*='goods']", "a[href*='/product']", "a"):
                try:
                    first_link = first_card.find_element(By.CSS_SELECTOR, link_sel)
                    if first_link and first_link.get_attribute("href"):
                        log(f"  카드={sel} / 링크={link_sel} href={first_link.get_attribute('href')}")
                        break
                    first_link = None
                except Exception:
                    continue
            if first_link:
                break
        except Exception:
            continue

    if not first_link:
        log("  ! 상품 카드/링크를 못 찾음")
        return False

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});",
            first_link,
        )
        time.sleep(0.3)
        driver.execute_script("arguments[0].click();", first_link)
        wait_for_ready(driver, timeout_sec=15)
        dump_page(driver, out_dir, "03_product_detail")
        return True
    except Exception as exc:
        log(f"  ! 클릭 실패: {exc}")
        save_text(out_dir / "open_first_error.txt", traceback.format_exc())
        return False


def dump_buy_button_candidates(driver, out_dir: Path) -> None:
    log("구매하기 후보 버튼 dump")
    lines: list[str] = []

    # 1) 텍스트 기반 후보
    for hint in BUY_BUTTON_HINT_TEXTS:
        try:
            xpath = (
                f"//*[self::a or self::button or self::span or self::div]"
                f"[normalize-space(text())='{hint}' or contains(normalize-space(.),'{hint}')]"
            )
            els = driver.find_elements(By.XPATH, xpath)
            for el in els[:10]:
                try:
                    outer = el.get_attribute("outerHTML") or ""
                    tag = el.tag_name
                    cls = el.get_attribute("class") or ""
                    txt = (el.text or "").strip()
                    visible = el.is_displayed()
                    lines.append(
                        f"--- hint='{hint}' tag={tag} class='{cls}' visible={visible} text='{txt[:80]}'\n{outer[:600]}"
                    )
                except Exception:
                    continue
        except Exception:
            continue

    # 2) class/id 기반 후보
    for sel in [
        "a.btn-buy", "button.btn-buy", "[class*='btn-buy']",
        "a.btn-order", "button.btn-order", "[class*='btn-order']",
        "[class*='Purchase']", "[class*='purchase']",
        "[onclick*='buy']", "[onclick*='order']",
        "#btnBuy", "#btnOrder",
    ]:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els[:5]:
                try:
                    outer = el.get_attribute("outerHTML") or ""
                    visible = el.is_displayed()
                    lines.append(f"--- selector='{sel}' visible={visible}\n{outer[:600]}")
                except Exception:
                    continue
        except Exception:
            continue

    save_text(out_dir / "04_buy_button_candidates.txt", "\n\n".join(lines) if lines else "(no candidates found)")


def _try_click(driver, el, label: str) -> bool:
    """selenium native click → JS click → native MouseEvent dispatch 순서로 클릭 시도."""
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'instant',block:'center'});", el
        )
        time.sleep(0.2)
    except Exception:
        pass

    # 1) selenium native click
    try:
        el.click()
        log(f"  native click OK ({label})")
        return True
    except Exception as exc:
        log(f"  native click 실패 ({label}): {exc}")

    # 2) JS click
    try:
        driver.execute_script("arguments[0].click();", el)
        log(f"  js click OK ({label})")
        return True
    except Exception as exc:
        log(f"  js click 실패 ({label}): {exc}")

    # 3) MouseEvent dispatch (React 합성 이벤트 트리거용)
    try:
        driver.execute_script(
            "arguments[0].dispatchEvent(new MouseEvent('click', "
            "{bubbles:true, cancelable:true, view:window, button:0}));",
            el,
        )
        log(f"  dispatch click OK ({label})")
        return True
    except Exception as exc:
        log(f"  dispatch click 실패 ({label}): {exc}")

    return False


def _find_buy_button(driver):
    # 1) hint 텍스트 기반 — 정확한 button/anchor 만 매칭(상위 div가 잡히지 않도록)
    for hint in BUY_BUTTON_HINT_TEXTS:
        xpath = (
            f"//button[normalize-space(.)='{hint}']"
            f" | //a[normalize-space(.)='{hint}']"
        )
        try:
            for el in driver.find_elements(By.XPATH, xpath):
                if el.is_displayed():
                    return el, f"text='{hint}'"
        except Exception:
            continue
    # 2) class 기반
    for sel in [
        "button.Button_primary__aI9o6.Button_large__EWW0F",  # 캡쳐 v3 에서 확인된 패턴
        "[class*='DetailCTA_buttonArea'] button[class*='Button_primary']",
        "[class*='ButtonArea_sticky'] button[class*='Button_primary']",
        "a.btn-buy", "button.btn-buy", "[class*='btn-buy']", "[class*='btn-order']",
    ]:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                if el.is_displayed() and (el.text or "").strip().startswith("구매"):
                    return el, f"sel='{sel}'"
        except Exception:
            continue
    return None, None


def _dump_option_layer(driver, out_dir: Path, suffix: str) -> int:
    """현재 DOM 에서 옵션 레이어 후보들을 dump. 매치 개수 반환."""
    layer_lines: list[str] = []
    total = 0
    for sel in OPTION_LAYER_HINT_SELECTORS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els[:3]:
                try:
                    outer = el.get_attribute("outerHTML") or ""
                    visible = el.is_displayed()
                    layer_lines.append(
                        f"--- selector='{sel}' visible={visible} len={len(outer)}\n{outer[:5000]}"
                    )
                    total += 1
                except Exception:
                    continue
        except Exception:
            continue
    save_text(
        out_dir / f"06_option_layer_candidates_{suffix}.txt",
        "\n\n".join(layer_lines) if layer_lines else "(no layer candidates found)",
    )
    return total


def _dump_keyword_context(driver, out_dir: Path, suffix: str) -> None:
    """'사이즈/SIZE/수량/선택' 텍스트 주변 컨텍스트를 직접 dump (Portal 마크업 탐지용)."""
    js = r"""
    const keys = ['사이즈','SIZE','수량','선택','품절','재고'];
    const out = [];
    const all = document.querySelectorAll('button, [role="button"], li, span, strong, div, label');
    for (const el of all) {
      const t = (el.innerText || el.textContent || '').trim();
      if (!t || t.length > 60) continue;
      for (const k of keys) {
        if (t === k || t.startsWith(k) || t.endsWith(k)) {
          const rect = el.getBoundingClientRect();
          out.push({
            tag: el.tagName.toLowerCase(),
            cls: el.getAttribute('class') || '',
            aria: el.getAttribute('aria-label') || '',
            text: t.slice(0,120),
            visible: rect.width>0 && rect.height>0,
            outer: el.outerHTML.slice(0,800),
          });
          break;
        }
      }
      if (out.length > 40) break;
    }
    return JSON.stringify(out, null, 2);
    """
    try:
        result = driver.execute_script(js)
    except Exception as exc:
        result = f"(eval failed: {exc})"
    save_text(out_dir / f"07_keyword_context_{suffix}.txt", result or "(empty)")


def click_buy_and_dump_layer(driver, out_dir: Path) -> None:
    log("구매하기 클릭 시도 + 옵션 레이어 dump")
    el, label = _find_buy_button(driver)
    if not el:
        log("  ! 구매하기 버튼을 찾지 못함")
        save_text(out_dir / "05_buy_click_skipped.txt", "no buy button matched")
        return

    log(f"  대상 버튼 매치: {label}")
    clicked = _try_click(driver, el, label)
    if not clicked:
        log("  ! 모든 클릭 방식 실패")

    # 폴링: 1s / 3s / 5s 시점에 스냅샷
    for i, wait_s in enumerate([1.0, 2.0, 2.0], start=1):
        time.sleep(wait_s)
        tag = f"t{i}"
        dump_page(driver, out_dir, f"05_after_buy_click_{tag}")
        n = _dump_option_layer(driver, out_dir, tag)
        _dump_keyword_context(driver, out_dir, tag)
        log(f"  스냅샷 {tag}: 옵션 레이어 후보 {n}개")


def main() -> int:
    parser = argparse.ArgumentParser(description="thehyundai.com DOM capture (one-shot)")
    parser.add_argument("--keyword", required=True, help="검색 키워드 (상품명 또는 상품코드)")
    parser.add_argument(
        "--out",
        default=None,
        help="산출물 디렉터리(기본: /tmp/hyundai_capture/<timestamp>)",
    )
    parser.add_argument(
        "--entry-url",
        default=os.getenv("HYUNDAI_ENTRY_URL", DEFAULT_ENTRY_URL),
        help=f"진입 URL (기본: {DEFAULT_ENTRY_URL}, 환경변수 HYUNDAI_ENTRY_URL 로도 지정 가능)",
    )
    parser.add_argument("--login", action="store_true", help="HYUNDAI_LOGIN_ID/PW로 사전 로그인 시도")
    parser.add_argument(
        "--ua",
        default=None,
        help="User-Agent 오버라이드 (예: 모바일 UA). 지정 시 CHROME_UA 환경변수로 주입",
    )
    args = parser.parse_args()

    if args.ua:
        os.environ["CHROME_UA"] = args.ua

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else Path("/tmp/hyundai_capture") / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"산출물 디렉터리: {out_dir}")
    log(f"legacy HYUNDAI_HOME(참고용): {LEGACY_HYUNDAI_HOME}")
    log(f"이번 entry URL: {args.entry_url}")

    driver = None
    try:
        driver = create_driver()
        # 후보 셀렉터 fallback 반복으로 매번 implicit_wait 가 누적되지 않도록
        # 캡쳐 시에는 implicit_wait 를 최소화한다.
        try:
            driver.implicitly_wait(0)
        except Exception:
            pass

        if args.login:
            try_login(driver, out_dir)

        if not do_search(driver, args.entry_url, args.keyword, out_dir):
            log("검색 실패 — 산출물만 남기고 종료")
            return 2

        if not open_first_product(driver, out_dir):
            log("상세 진입 실패 — 산출물만 남기고 종료")
            return 3

        dump_buy_button_candidates(driver, out_dir)
        click_buy_and_dump_layer(driver, out_dir)

        log("캡쳐 완료")
        return 0
    except Exception as exc:
        log(f"치명적 예외: {exc}")
        save_text(out_dir / "fatal_error.txt", traceback.format_exc())
        return 1
    finally:
        safe_quit_driver(driver)


if __name__ == "__main__":
    raise SystemExit(main())
