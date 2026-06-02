"""Drive the app in an iPhone-12-emulated Chromium and screenshot every screen.
Reports PASS/FAIL per step; never aborts on a single failure."""
import re, os, pathlib, sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:" + os.environ.get("REVIEW_PORT", "8000")
OUT = pathlib.Path("screenshots"); OUT.mkdir(exist_ok=True)
results = []

def ok(name, cond, info=""):
    results.append((name, cond, info))
    print(("OK  " if cond else "FAIL") + " " + name + (("  " + info) if info else ""))

def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))

def login(page, email):
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill("input[name=email]", email)
    page.click("button:has-text('Send me a code')")
    page.wait_for_selector("input[name=code]", timeout=8000)
    body = page.inner_text("body")
    m = re.search(r"code is\s*(\d{6})", body) or re.search(r"\b(\d{6})\b", body)
    page.fill("input[name=code]", m.group(1))
    page.click("button:has-text('Sign me in')")
    page.wait_for_url(re.compile(r".*/(capture|onboarding)"), timeout=8000)
    page.wait_for_load_state("networkidle")

with sync_playwright() as p:
    iphone = dict(p.devices["iPhone 12"]); iphone.pop("default_browser_type", None)
    browser = p.chromium.launch()

    # ============ FLOW A — brand new user: onboarding + empty states ============
    try:
        ctx = browser.new_context(**iphone)
        a = ctx.new_page()
        a.goto(BASE + "/login", wait_until="networkidle")
        shot(a, "01_login_email")
        a.fill("input[name=email]", "newuser@example.com")
        a.click("button:has-text('Send me a code')")
        a.wait_for_selector("input[name=code]", timeout=8000)
        shot(a, "02_login_code")
        body = a.inner_text("body")
        code = (re.search(r"code is\s*(\d{6})", body) or re.search(r"\b(\d{6})\b", body)).group(1)
        a.fill("input[name=code]", code)
        a.click("button:has-text('Sign me in')")
        a.wait_for_url(re.compile(r".*/(onboarding|capture)"), timeout=8000)
        ok("login new user -> onboarding", "/onboarding" in a.url, a.url)
        if "/onboarding" in a.url:
            a.wait_for_timeout(400); shot(a, "03_onboarding")
            # advance through cards: click the visible primary button up to 4x
            for _ in range(5):
                if "/capture" in a.url:
                    break
                btns = a.query_selector_all("button:visible, a.btn:visible")
                clicked = False
                for b in btns:
                    t = (b.inner_text() or "").lower()
                    if any(k in t for k in ("start", "next", "continue", "got it", "begin", "let")):
                        b.click(); clicked = True; break
                if not clicked and btns:
                    btns[-1].click()
                a.wait_for_timeout(500)
            ok("finish onboarding -> capture", "/capture" in a.url, a.url)
        a.wait_for_timeout(400); shot(a, "04_capture_empty")
        # manual expense
        a.goto(BASE + "/capture/manual", wait_until="networkidle"); a.wait_for_timeout(300)
        shot(a, "05_manual_expense")
        ctx.close()
    except Exception as e:
        ok("Flow A", False, repr(e))

    # ============ FLOW B — demo user: rich data + full scan flow ============
    try:
        ctx = browser.new_context(**iphone)
        b = ctx.new_page()
        login(b, "demo@example.com")
        ok("login demo -> capture", "/capture" in b.url, b.url)
        b.wait_for_timeout(700)  # let /status/line lazy-load
        shot(b, "06_capture_home")

        # full scan pipeline via the hidden file input
        try:
            b.set_input_files("#receipt", "screenshots/test_receipt.jpg")
            b.wait_for_selector("text=Looks right", timeout=12000)
            b.wait_for_timeout(400); shot(b, "07_confirm")
            ok("scan -> confirm screen", True)
            b.click("button:has-text('Looks right')")
            b.wait_for_selector("text=Saved", timeout=8000)
            b.wait_for_timeout(300); shot(b, "08_saved")
            ok("save scanned expense", True)
        except Exception as e:
            ok("scan flow", False, repr(e)); shot(b, "07_confirm_fail")

        # records
        b.goto(BASE + "/records", wait_until="networkidle"); b.wait_for_timeout(400)
        shot(b, "09_records"); ok("records page", "2026/27" in b.inner_text("body") or "2025/26" in b.inner_text("body"))
        # open a month (find the month link: /records/<id>/<YYYY-MM>)
        hrefs = b.eval_on_selector_all("a[href*='/records/']",
                                       "els => els.map(e => e.getAttribute('href'))")
        month_href = next((h for h in hrefs if re.search(r"/records/[0-9a-f]+/\d{4}-\d{2}", h or "")), None)
        if month_href:
            b.goto(BASE + month_href, wait_until="networkidle")
        b.wait_for_timeout(400); shot(b, "10_records_month")
        body = b.inner_text("body")
        ok("records month view", "in" in body.lower() and ("out" in body.lower()))
        # inline edit on first entry
        try:
            b.click("button:has-text('Edit')", timeout=5000)
            b.wait_for_selector("input[name=amount]", timeout=5000)
            b.wait_for_timeout(400); shot(b, "11_entry_edit")
            ok("inline edit opens", True)
        except Exception as e:
            ok("inline edit", False, repr(e))

        # repeats — suggestions detected from history + tap-to-log
        b.goto(BASE + "/repeated", wait_until="networkidle"); b.wait_for_timeout(400)
        shot(b, "18_repeats")
        ok("repeats page", "repeat" in b.inner_text("body").lower())
        try:
            b.click("button:has-text('Set up')", timeout=4000)
            b.wait_for_timeout(500); shot(b, "19_repeats_after_setup")
            b.click("button:has-text('Log')", timeout=4000)
            b.wait_for_selector("text=Saved", timeout=6000)
            b.wait_for_timeout(300); shot(b, "20_repeat_logged")
            ok("repeat set-up + tap-to-log", True)
        except Exception as e:
            ok("repeat set-up + tap-to-log", False, repr(e))

        # status
        b.goto(BASE + "/status", wait_until="networkidle"); b.wait_for_timeout(400)
        shot(b, "12_status"); ok("status page", "where you stand" in b.inner_text("body").lower() or "£" in b.inner_text("body"))

        # categories
        b.goto(BASE + "/categories", wait_until="networkidle"); b.wait_for_timeout(400)
        shot(b, "13_categories"); ok("categories page", "categor" in b.inner_text("body").lower())

        # export -> make pack
        b.goto(BASE + "/export", wait_until="networkidle"); b.wait_for_timeout(400)
        shot(b, "14_export")
        try:
            b.click("button:has-text('Make pack')", timeout=4000)
            b.wait_for_selector("text=ready", timeout=10000)
            b.wait_for_timeout(400); shot(b, "15_export_ready")
            ok("export pack built", True)
        except Exception as e:
            ok("export pack", False, repr(e))

        # account
        b.goto(BASE + "/account", wait_until="networkidle"); b.wait_for_timeout(400)
        shot(b, "16_account"); ok("account page", "account" in b.inner_text("body").lower())

        # more hub
        b.goto(BASE + "/more", wait_until="networkidle"); b.wait_for_timeout(300)
        shot(b, "17_more")
        ctx.close()
    except Exception as e:
        ok("Flow B", False, repr(e))

    browser.close()

passed = sum(1 for _, c, _ in results if c)
print(f"\n{passed}/{len(results)} steps OK")
print("Screenshots:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))
sys.exit(0 if passed == len(results) else 1)
