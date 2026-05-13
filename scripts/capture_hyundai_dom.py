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
    HYUNDAI_HOME,
    create_driver,
    safe_quit_driver,
    wait_for_ready,
)


BUY_BUTTON_HINT_TEXTS = ["구매하기", "바로구매", "BUY NOW", "구매 하기", "구매"]
OPTION_LAYER_HINT_SELECTORS = [
    "ul.opt-select-layer",
    ".opt-select-layer",
    ".option-layer",
    ".product-option-layer",
    "[class*='opt-select']",
    "[class*='option-layer']",
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


def do_search(driver, keyword: str, out_dir: Path) -> bool:
    log(f"검색: {keyword}")
    try:
        driver.get(HYUNDAI_HOME)
        wait_for_ready(driver, timeout_sec=10)
        dump_page(driver, out_dir, "01_home")

        # 기존 셀렉터 + 신규 후보들을 차례로 시도
        candidates = [
            (By.ID, "cs-token-input"),
            (By.CSS_SELECTOR, "input[type='search']"),
            (By.CSS_SELECTOR, "input[placeholder*='검색']"),
            (By.CSS_SELECTOR, "input[name='searchTerm']"),
            (By.CSS_SELECTOR, "input.search-input"),
            (By.CSS_SELECTOR, "[data-testid*='search'] input"),
        ]

        box = None
        for by, sel in candidates:
            try:
                box = WebDriverWait(driver, 4).until(
                    EC.presence_of_element_located((by, sel))
                )
                if box:
                    log(f"  검색창 발견: {by}={sel}")
                    break
            except Exception:
                continue
        if not box:
            log("  ! 검색창을 못 찾음 - 01_home.html 로 확인 필요")
            return False

        try:
            box.clear()
        except Exception:
            pass
        box.send_keys(keyword)
        box.send_keys(Keys.RETURN)
        wait_for_ready(driver, timeout_sec=10)
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
            for link_sel in ("a.title.ellipsis", "a.title", "a[href*='goods']", "a"):
                try:
                    first_link = cards[0].find_element(By.CSS_SELECTOR, link_sel)
                    if first_link and first_link.get_attribute("href"):
                        log(f"  카드={sel} / 링크={link_sel}")
                        break
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


def click_buy_and_dump_layer(driver, out_dir: Path) -> None:
    log("구매하기 클릭 시도 + 옵션 레이어 dump")
    clicked = False
    # 텍스트 기반 클릭부터 시도
    for hint in BUY_BUTTON_HINT_TEXTS:
        try:
            xpath = (
                f"//a[normalize-space(text())='{hint}']"
                f" | //button[normalize-space(text())='{hint}']"
                f" | //span[normalize-space(text())='{hint}']/.."
            )
            els = driver.find_elements(By.XPATH, xpath)
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView({behavior:'instant',block:'center'});", el
                    )
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", el)
                    clicked = True
                    log(f"  클릭 성공 hint='{hint}'")
                    break
                except Exception:
                    continue
            if clicked:
                break
        except Exception:
            continue

    if not clicked:
        log("  ! 텍스트 기반 구매하기 클릭 실패 — class 기반 fallback 시도")
        for sel in ["a.btn-buy", "button.btn-buy", "[class*='btn-buy']", "[class*='btn-order']"]:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if not el.is_displayed():
                        continue
                    try:
                        driver.execute_script("arguments[0].click();", el)
                        clicked = True
                        log(f"  fallback 클릭 성공 selector='{sel}'")
                        break
                    except Exception:
                        continue
                if clicked:
                    break
            except Exception:
                continue

    # 클릭 직후 상태 1차 캡쳐 (로그인 페이지로 리다이렉트되었을 수도 있음)
    time.sleep(1.2)
    dump_page(driver, out_dir, "05_after_buy_click")

    # 옵션 레이어 후보 dump
    log("옵션 레이어 후보 dump")
    layer_lines: list[str] = []
    for sel in OPTION_LAYER_HINT_SELECTORS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els[:3]:
                try:
                    outer = el.get_attribute("outerHTML") or ""
                    visible = el.is_displayed()
                    layer_lines.append(
                        f"--- selector='{sel}' visible={visible} len={len(outer)}\n{outer[:4000]}"
                    )
                except Exception:
                    continue
        except Exception:
            continue
    save_text(
        out_dir / "06_option_layer_candidates.txt",
        "\n\n".join(layer_lines) if layer_lines else "(no layer candidates found)",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="thehyundai.com DOM capture (one-shot)")
    parser.add_argument("--keyword", required=True, help="검색 키워드 (상품명 또는 상품코드)")
    parser.add_argument(
        "--out",
        default=None,
        help="산출물 디렉터리(기본: /tmp/hyundai_capture/<timestamp>)",
    )
    parser.add_argument("--login", action="store_true", help="HYUNDAI_LOGIN_ID/PW로 사전 로그인 시도")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else Path("/tmp/hyundai_capture") / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"산출물 디렉터리: {out_dir}")

    driver = None
    try:
        driver = create_driver()

        if args.login:
            try_login(driver, out_dir)

        if not do_search(driver, args.keyword, out_dir):
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
