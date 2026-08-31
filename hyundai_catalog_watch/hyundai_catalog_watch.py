#!/usr/bin/env python3
"""
Hyundai(더현대) RRL 카탈로그 재고 감시 — 컬티즘 rrl-catalog-watch 의 더현대 버전.

동작(1 사이클):
  1) 디스커버리(HTTP): 더현대 검색 API 로 현재 '구매가능' RRL 제품 전체 목록 수집
     (더현대는 품절 상품을 검색에서 제외 -> 목록에 있으면 = 적어도 1사이즈 구매가능)
  2) 사이즈 스윕(selenium): 목록의 각 제품 상세 -> 구매하기 -> 사이즈+수량 판독
     (드라이버를 워커당 1회만 로그인해 재사용, ~7.7초/개)
  3) 이전 스냅샷(state.json)과 diff -> 변동 시 텔레그램 알림
       - 새 제품(코드) 등장         : 🆕 새 제품 구매가능 (+사이즈)
       - 사라졌다 재등장(전체 품절->재입고): 🟢 재입고 (+사이즈)
       - 기존 제품의 특정 사이즈가 품절->구매가능: 🟢 사이즈 재입고
     첫 실행은 baseline(무알림).

재사용: 사이즈 판독은 검증된 stock_check.hyundai.stock_checker 함수를 그대로 import.
알림: shared/.env 의 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (rrl_stock_bot).
"""
import os
import re
import sys
import json
import time
import html
import urllib.parse
import urllib.request
import urllib.error
import concurrent.futures
from datetime import datetime, timezone, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_CHECK_DIR = os.getenv("STOCK_CHECK_DIR", "/opt/stock_check")
sys.path.insert(0, STOCK_CHECK_DIR)

# --- .env 로드 (로그인 자격증명 + 텔레그램) ---
ENV_PATH = os.getenv("HYUNDAI_ENV", os.path.join(STOCK_CHECK_DIR, "stock_check/shared/.env"))
try:
    for _line in open(ENV_PATH, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
except Exception as _e:
    print(f"[warn] .env 로드 실패: {_e}")

os.environ.setdefault("CHROME_HEADLESS_MODE", "new")

# stock_checker 는 import 시 signal.signal(SIGTERM/SIGINT) 을 등록한다(모듈 최상위).
# signal 은 메인 스레드에서만 가능하므로 반드시 여기(메인 스레드)서 1회 import 해둔다.
# 이후 워커 스레드는 sys.modules 캐시된 이 모듈을 그대로 사용(재등록 없음).
from stock_check.hyundai import stock_checker as sc  # noqa: E402

STATE_PATH = os.path.join(APP_DIR, "state.json")
LOG_PATH = os.path.join(APP_DIR, "watch.log")
SEARCH_API = "https://hi.thehyundai.com/proxy/v1/dp/search/searchResult"
SEARCH_QUERY = os.getenv("HCW_QUERY", "RRL")
PAGE_SIZE = 36
MAX_PAGES = int(os.getenv("HCW_MAX_PAGES", "10"))
WORKERS = max(1, min(int(os.getenv("HCW_WORKERS", "2")), 4))
DRY_RUN = os.getenv("HCW_DRY_RUN", "0") in ("1", "true", "yes")
KST = timezone(timedelta(hours=9))

_CODE_RE = re.compile(r"\(([A-Z0-9]{8,})\)")


def log(msg):
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---- 크로스프로세스 락 (full/discover 동시 실행 방지, mkdir 원자적) ----
LOCK_DIR = os.getenv("HCW_LOCK", "/tmp/hyundai-catalog-watch.lock")
LOCK_STALE_SEC = 1200  # 20분 넘은 락은 죽은 것으로 간주


def acquire_lock():
    try:
        os.mkdir(LOCK_DIR)
        return True
    except FileExistsError:
        try:
            if time.time() - os.stat(LOCK_DIR).st_mtime > LOCK_STALE_SEC:
                os.rmdir(LOCK_DIR)
                os.mkdir(LOCK_DIR)
                return True
        except Exception:
            pass
        return False
    except Exception:
        return True  # 락 자체 실패 시 진행(감시 우선)


def release_lock():
    try:
        os.rmdir(LOCK_DIR)
    except Exception:
        pass


# ---------------- 디스커버리 (HTTP) ----------------
def _api_page(page):
    q = urllib.parse.urlencode({
        "searchQuery": SEARCH_QUERY,
        "searchType": "NCP_PRODUCT",
        "page": page,
        "disPlaySize": PAGE_SIZE,
    })
    url = f"{SEARCH_API}?{q}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": f"https://hi.thehyundai.com/search?tab=product&q={urllib.parse.quote(SEARCH_QUERY)}",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def style_code(name):
    """제품명 끝의 (스타일코드) 추출. 예: 'RRL ...(MNRRKNI16820181410)' -> MNRRKNI16820181410"""
    m = _CODE_RE.search(name or "")
    return m.group(1) if m else None


def discover():
    """현재 구매가능한 RRL 제품 목록. 키 = slitmCd(더현대 고유 상품코드, 유니크).
    반환: dict[slitmCd] = {name, slitmCd, style_code, price, url}"""
    products = {}
    total = None
    for page in range(1, MAX_PAGES + 1):
        try:
            j = _api_page(page)
        except Exception as e:
            log(f"디스커버리 page {page} 실패: {e}")
            break
        pl = (j.get("data") or {}).get("productList") or {}
        total = pl.get("totalSize")
        infos = pl.get("productInfoList") or []
        for it in infos:
            slitm = it.get("slitmCd")
            if not slitm:
                continue
            nm = it.get("slitmNm") or ""
            products[slitm] = {
                "name": nm,
                "slitmCd": slitm,
                "style_code": style_code(nm),  # RRL 스타일코드(표시/참고용, 없을 수 있음)
                "price": it.get("sellPrc") or it.get("bnftPrc"),
                "url": f"https://hi.thehyundai.com/product/{slitm}",
            }
        if len(infos) < PAGE_SIZE:
            break
    log(f"디스커버리: 구매가능 RRL {len(products)}개 (API totalSize={total})")
    return products


# ---------------- 사이즈 판독 (selenium, 워커풀) ----------------
# 로그인 상세페이지의 '상품 쿠폰 할인' 섹션 파싱용 JS.
# 회원쿠폰(매니아 % 쿠폰 등)은 로그인 상태 상세페이지에만 뜸(비로그인 API엔 없음).
_COUPON_JS = r"""() => {
  const labels = Array.from(document.querySelectorAll("[class*='BenefitCalc_label']"));
  const coupons = Array.from(new Set(
    labels.map(l => (l.textContent || '').trim()).filter(t => t.includes('쿠폰'))
  ));
  let maxBenefit = null;
  const bt = (document.body && document.body.innerText) || '';
  const m = bt.match(/최대혜택가\s*([0-9,]+)\s*원/);
  if (m) maxBenefit = parseInt(m[1].replace(/,/g, ''), 10);
  return {coupons: coupons, maxBenefit: maxBenefit};
}"""


def _parse_coupon(driver):
    """상세페이지에서 쿠폰 적용여부 파싱. 반환 {applicable, coupons:[명칭], maxBenefit} 또는 None.
    혜택영역 렌더 지연 대비 폴링(쿠폰 or 최대혜택가 뜨면 즉시 종료 → 대개 <1s, 최대 HCW_COUPON_WAIT_SEC)."""
    deadline = time.time() + float(os.getenv("HCW_COUPON_WAIT_SEC", "4"))
    r = {}
    while True:
        try:
            r = driver.execute_script("return (" + _COUPON_JS + ")()") or {}
        except Exception:
            r = {}
        if r.get("coupons") or r.get("maxBenefit") is not None or time.time() >= deadline:
            break
        time.sleep(0.4)
    coupons = r.get("coupons") or []
    return {"applicable": len(coupons) > 0, "coupons": coupons, "maxBenefit": r.get("maxBenefit")}


def _read_one(driver, slitm):
    """slitmCd 로 로그인 상세페이지 직접 이동 → 쿠폰 섹션 파싱 + 사이즈/수량 판독.
    반환 {"sizes": list|None, "coupon": dict|None} 또는 None(페이지 로드 자체 실패)."""
    try:
        driver.get(f"https://hi.thehyundai.com/product/{slitm}")
        sc.wait_for_ready(driver, timeout_sec=int(os.getenv("HCW_READY_SEC", "12")))
        time.sleep(float(os.getenv("HCW_HYDRATION_SEC", "1.5")))
        coupon = _parse_coupon(driver)  # 메인 페이지의 '상품 쿠폰 할인' 섹션(드로어 열기 전)
        sizes = None
        if sc.click_buy_and_open_size_list(driver):
            sizes = sc.get_size_stocks(driver)
        return {"sizes": sizes, "coupon": coupon}
    except Exception as e:
        log(f"[{slitm}] 판독 예외: {str(e)[:100]}")
        return None


def _worker(codes):
    """워커 1개: 드라이버 1개 생성+로그인 후 배정된 코드들을 순회 판독. (sc 는 메인스레드서 import된 전역)"""
    out = {}
    driver = None
    try:
        driver = sc.create_driver()
        sc.ensure_logged_in(driver)
        for code in codes:
            out[code] = _read_one(driver, code)
    except Exception as e:
        log(f"워커 예외: {str(e)[:120]}")
    finally:
        if driver is not None:
            try:
                sc.safe_quit_driver(driver)
            except Exception:
                pass
    return out


def sweep_sizes(products):
    """products 의 모든 코드에 대해 사이즈+쿠폰 판독. 반환 dict[code] = {"sizes":list|None,"coupon":dict|None} | None"""
    codes = list(products.keys())
    if not codes:
        return {}
    # 워커별로 코드 라운드로빈 분배
    buckets = [[] for _ in range(WORKERS)]
    for i, c in enumerate(codes):
        buckets[i % WORKERS].append(c)
    results = {}
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_worker, b) for b in buckets if b]
        for f in concurrent.futures.as_completed(futs):
            try:
                results.update(f.result() or {})
            except Exception as e:
                log(f"워커 결과 수집 실패: {str(e)[:100]}")
    ok = sum(1 for v in results.values() if v and v.get("sizes"))
    cpn = sum(1 for v in results.values() if v and (v.get("coupon") or {}).get("applicable"))
    log(f"사이즈 스윕: {len(codes)}개 중 {ok}개 판독 성공 / 쿠폰적용 {cpn}개 ({time.time()-t0:.0f}초, 워커 {WORKERS})")
    return results


# ---------------- 스냅샷/diff ----------------
def sizes_to_map(size_stocks):
    """[{size,qty,soldout}] -> {size: {'qty':n,'soldout':bool}}  (구매가능 사이즈만 True 판정용)"""
    m = {}
    for s in size_stocks or []:
        name = (s.get("size") or "").strip()
        if not name:
            continue
        soldout = bool(s.get("soldout")) or (s.get("qty") == 0)
        m[name] = {"qty": s.get("qty"), "soldout": soldout}
    return m


def available_sizes(size_map):
    return sorted([k for k, v in size_map.items() if not v.get("soldout")])


def build_snapshot(products, size_results, prev):
    """현재 스냅샷 구성. 판독 실패/부분실패한 제품은 이전 값 유지(오탐 방지).
    size_results[code] = {"sizes":list|None, "coupon":dict|None} | None."""
    prev_products = (prev or {}).get("products", {})
    snap = {}
    now = int(time.time())
    for code, meta in products.items():
        res = size_results.get(code)
        prev_e = prev_products.get(code) or {}
        if res is None:  # 페이지 로드 자체 실패 -> 이전값 유지
            sizes = prev_e.get("sizes", {})
            coupon = prev_e.get("coupon")
            read_ok = False
        else:
            szlist = res.get("sizes")
            sizes = sizes_to_map(szlist) if szlist is not None else prev_e.get("sizes", {})
            coupon = res.get("coupon")
            if coupon is None:  # 쿠폰 파싱 실패 -> 이전 유지
                coupon = prev_e.get("coupon")
            read_ok = szlist is not None
        snap[code] = {
            "name": meta["name"],
            "slitmCd": meta["slitmCd"],
            "price": meta["price"],
            "url": meta["url"],
            "sizes": sizes,
            "coupon": coupon,
            "read_ok": read_ok,
            "last_seen": now,
        }
    return snap


def diff_and_alerts(prev, snap, ever_seen):
    """이전 대비 알림 이벤트 리스트 생성. 반환 list[dict]"""
    events = []
    prev_products = (prev or {}).get("products", {})
    for code, cur in snap.items():
        cur_avail = available_sizes(cur["sizes"])
        if code not in prev_products:
            # 이전 목록에 없던 제품
            if code in ever_seen:
                events.append({"type": "restock_product", "code": code, "cur": cur, "sizes": cur_avail})
            else:
                events.append({"type": "new_product", "code": code, "cur": cur, "sizes": cur_avail})
        else:
            # 기존 제품: 사이즈 단위 품절->구매가능 전환 감지
            prev_avail = set(available_sizes(prev_products[code].get("sizes", {})))
            newly = [s for s in cur_avail if s not in prev_avail]
            if newly:
                events.append({"type": "restock_size", "code": code, "cur": cur, "sizes": newly})
    return events


def representative_coupon(snap):
    """RRL 쿠폰은 브랜드 공통 → 재고있는 대표 제품 1개의 쿠폰상태로 판정(제품별 알림 스팸 방지).
    반환 {applicable, coupons, maxBenefit, product, url} 또는 None."""
    rep = None
    for cur in snap.values():
        if available_sizes(cur.get("sizes") or {}) and cur.get("coupon") is not None:
            rep = cur
            break
    if rep is None:  # 재고 대표 없으면 쿠폰정보 있는 아무 제품
        for cur in snap.values():
            if cur.get("coupon") is not None:
                rep = cur
                break
    if rep is None:
        return None
    rc = rep["coupon"]
    return {"applicable": bool(rc.get("applicable")), "coupons": rc.get("coupons") or [],
            "maxBenefit": rc.get("maxBenefit"), "product": rep["name"], "url": rep["url"]}


# ---------------- 텔레그램 ----------------
def send_telegram(text):
    # 더현대 전용 봇 우선(HCW_*), 없으면 shared/.env 공용(TELEGRAM_*) 폴백
    token = os.getenv("HCW_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("HCW_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("텔레그램 토큰/챗 없음 — 발송 스킵")
        return False
    if DRY_RUN:
        log(f"[DRY_RUN] 텔레그램: {text[:120]}")
        return True
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        log(f"텔레그램 발송 실패: {str(e)[:120]}")
        return False


def send_email(subject, body):
    """shared/.env 의 SMTP 설정으로 이메일 발송(컬티즘/리바이스와 동일 채널)."""
    if os.getenv("HCW_EMAIL_ENABLE", "1") not in ("1", "true", "yes", "on"):
        return False
    host = os.getenv("SMTP_SERVER")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pw = os.getenv("SMTP_PASSWORD")
    rcpts = [r.strip() for r in (os.getenv("EMAIL_RECIPIENTS") or "").split(",") if r.strip()]
    if not (host and user and pw and rcpts):
        log("이메일 설정 불완전 — 발송 스킵")
        return False
    if DRY_RUN:
        log(f"[DRY_RUN] 이메일: {subject}")
        return True
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = ", ".join(rcpts)
        s = smtplib.SMTP(host, port, timeout=20)
        s.starttls()
        s.login(user, pw)
        s.sendmail(user, rcpts, msg.as_string())
        s.quit()
        return True
    except Exception as e:
        log(f"이메일 발송 실패: {str(e)[:120]}")
        return False


def fmt_sizes(cur, only=None):
    parts = []
    for s in (only if only is not None else available_sizes(cur["sizes"])):
        q = (cur["sizes"].get(s) or {}).get("qty")
        parts.append(f"{s}({q}개)" if q is not None else s)
    return ", ".join(parts) if parts else "(사이즈 정보 없음)"


def alert_text(ev):
    cur = ev["cur"]
    if ev["type"] == "coupon_on":
        c = cur.get("coupon") or {}
        cps = c.get("coupons") or []
        lines = ["🎟️ [더현대 RRL] 쿠폰 적용 가능", cur["name"],
                 "적용 쿠폰: " + (", ".join(cps) if cps else "쿠폰")]
        mb = c.get("maxBenefit")
        price = cur.get("price")
        if isinstance(mb, int) and isinstance(price, int) and mb < price:
            lines.append(f"정가 {price:,}원 → 최대혜택가 {mb:,}원")
        elif isinstance(mb, int):
            lines.append(f"최대혜택가 {mb:,}원")
        lines.append(cur["url"])
        return "\n".join(lines)
    if ev["type"] == "coupon_off":
        lines = ["🚫 [더현대 RRL] 쿠폰 적용 종료", cur["name"]]
        pcs = cur.get("prev_coupons") or []
        if pcs:
            lines.append("이전 적용쿠폰: " + ", ".join(pcs))
        lines.append("→ 현재 RRL 제품 쿠폰 미적용")
        lines.append(cur["url"])
        return "\n".join(lines)
    head = {
        "new_product": "🆕 [더현대 RRL] 새 제품 구매가능",
        "restock_product": "🟢 [더현대 RRL] 재입고 (전체품절→구매가능)",
        "restock_size": "🟢 [더현대 RRL] 사이즈 재입고",
    }[ev["type"]]
    price = f"{cur['price']:,}원" if isinstance(cur.get("price"), int) else str(cur.get("price"))
    lines = [head, cur["name"], f"가격: {price}"]
    if ev["type"] == "restock_size":
        # 이번에 새로 재입고된 사이즈 + 현재 전체 구매가능 사이즈(오해 방지)
        lines.append(f"재입고 사이즈: {fmt_sizes(cur, ev.get('sizes'))}")
        lines.append(f"전체 구매가능: {fmt_sizes(cur)}")
    else:
        lines.append(f"구매가능 사이즈: {fmt_sizes(cur, ev.get('sizes'))}")
    lines.append(cur["url"])
    return "\n".join(lines)


# ---------------- 메인 ----------------
def load_state():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:
        return None


def save_state(snap, ever_seen, extra=None):
    tmp = STATE_PATH + ".tmp"
    d = {
        "products": snap,
        "ever_seen": sorted(ever_seen),
        "updated": int(time.time()),
    }
    if extra:
        d.update(extra)
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


def _send_digest(kind, bodies):
    """같은 카테고리(재고/쿠폰) 이벤트들을 1통 이메일로 통합 발송. 발송=1/미발송=0."""
    if not bodies:
        return 0
    n = len(bodies)
    subject = bodies[0].split("\n", 1)[0] if n == 1 else f"[더현대 RRL] {kind} 알림 {n}건"
    combined = ("\n\n" + "-" * 30 + "\n\n").join(bodies)
    if n > 1:
        combined = f"이번 확인에서 {kind} {n}건의 변동이 있었습니다.\n\n" + combined
    return 1 if send_email(subject, combined) else 0


def _emit(events):
    """알림 발송: 텔레그램은 이벤트별, 이메일은 카테고리별(재고/쿠폰) 각각 1통으로 통합. 텔레그램 발송건수 반환."""
    tg = 0
    stock_bodies, coupon_bodies = [], []
    for ev in events:
        body = alert_text(ev)
        if send_telegram(body):
            tg += 1
        (coupon_bodies if ev["type"] in ("coupon_on", "coupon_off") else stock_bodies).append(body)
        log(f"  · {ev['type']} {ev['code']} 사이즈={ev.get('sizes')}")
        time.sleep(0.4)
    em = _send_digest("재고", stock_bodies) + _send_digest("쿠폰", coupon_bodies)
    if events:
        log(f"  발송: 텔레그램 {tg}건 / 이메일 {em}통(재고·쿠폰 분리)")
    return tg


def run_full():
    """전체 사이클: 디스커버리 + 전체 사이즈 스윕 + diff(제품/사이즈 단위) + 알림."""
    t0 = time.time()
    log("=" * 50)
    log("[full] 더현대 RRL 카탈로그 감시 시작")
    prev = load_state()
    first_run = prev is None
    ever_seen = set((prev or {}).get("ever_seen", []))

    products = discover()
    if not products:
        log("구매가능 제품 0개 — 디스커버리 실패 의심, 이번 사이클 중단(상태 유지)")
        return
    size_results = sweep_sizes(products)
    snap = build_snapshot(products, size_results, prev)

    # RRL 쿠폰(브랜드 공통) 대표신호 — 재고있는 대표 1개로 판정(제품별 스팸 방지)
    cur_coupon = representative_coupon(snap) or (prev or {}).get("rrl_coupon")
    prev_coupon = (prev or {}).get("rrl_coupon")

    if first_run:
        for code in snap:
            ever_seen.add(code)
        save_state(snap, ever_seen, {"rrl_coupon": cur_coupon})
        log(f"첫 실행 baseline 저장 ({len(snap)}개) — 무알림 (쿠폰적용={bool(cur_coupon and cur_coupon.get('applicable'))})")
        log(f"[full] 완료 ({time.time()-t0:.0f}초)")
        return

    events = diff_and_alerts(prev, snap, ever_seen)
    # 쿠폰 적용여부 전환이면 1회 알림(적용/해제 양방향)
    if cur_coupon and prev_coupon:
        was = bool(prev_coupon.get("applicable"))
        now = bool(cur_coupon.get("applicable"))
        if now != was:
            events.append({
                "type": "coupon_on" if now else "coupon_off",
                "code": "RRL",
                "cur": {
                    "name": cur_coupon.get("product") or "RRL 제품",
                    "url": cur_coupon.get("url") or "https://hi.thehyundai.com/",
                    "price": None, "coupon": cur_coupon,
                    "prev_coupons": prev_coupon.get("coupons") or [],
                },
            })
    for code in snap:
        ever_seen.add(code)
    log(f"[full] 변동 이벤트: {len(events)}건 (쿠폰적용={bool(cur_coupon and cur_coupon.get('applicable'))})")
    sent = _emit(events)
    save_state(snap, ever_seen, {"rrl_coupon": cur_coupon})
    log(f"[full] 완료: 이벤트 {len(events)} / 발송 {sent} ({time.time()-t0:.0f}초)")


def run_discover():
    """경량 사이클(10분): HTTP 디스커버리로 '새 제품/재입고(제품단위)'만 빠르게 감지.
    신규 slitmCd 만 사이즈 판독(few) 후 알림·상태 병합. 기존 제품 사이즈는 건드리지 않음
    (사이즈 단위 재입고는 full 사이클이 담당). baseline 없으면 스킵(full 이 먼저 만들어야 함)."""
    t0 = time.time()
    log("[discover] 경량 디스커버리 시작")
    prev = load_state()
    if prev is None:
        log("[discover] baseline 없음 — full 사이클 먼저 필요, 스킵")
        return
    products = discover()
    if not products:
        log("[discover] 구매가능 0개 — 스킵(상태 유지)")
        return
    prev_products = prev.get("products", {})
    ever_seen = set(prev.get("ever_seen", []))
    new_ids = [sid for sid in products if sid not in prev_products]
    log(f"[discover] 신규(제품단위) 후보: {len(new_ids)}개")
    if not new_ids:
        log(f"[discover] 변동 없음 ({time.time()-t0:.0f}초)")
        return
    subset = {sid: products[sid] for sid in new_ids}
    size_results = sweep_sizes(subset)
    snap_new = build_snapshot(subset, size_results, prev)
    events = diff_and_alerts(prev, snap_new, ever_seen)
    prev_products.update(snap_new)         # 신규만 병합(기존 제거 안 함 — full 이 정리)
    for sid in snap_new:
        ever_seen.add(sid)
    sent = _emit(events)
    save_state(prev_products, ever_seen, {"rrl_coupon": prev.get("rrl_coupon")})
    log(f"[discover] 완료: 신규 {len(new_ids)} / 이벤트 {len(events)} / 발송 {sent} ({time.time()-t0:.0f}초)")


def main():
    mode = os.getenv("HCW_MODE", "full").strip().lower()
    if not acquire_lock():
        log(f"[{mode}] 다른 사이클 실행 중 — 스킵")
        return
    try:
        if mode == "discover":
            run_discover()
        else:
            run_full()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
