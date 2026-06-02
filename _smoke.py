"""End-to-end integration smoke test across ALL slices. Asserts HTMX swap
responses are clean fragments (no nested shell)."""
import os, re, sys
os.environ["DATABASE_URL"] = "sqlite:///./_smoke.db"
os.environ["SEED_DEMO_USERS"] = "true"
os.environ["DEV_SHOW_OTP"] = "true"

from fastapi.testclient import TestClient
from api.index import app

HX = {"hx-request": "true"}
fails = []

def check(name, cond, extra=""):
    print(("OK  " if cond else "FAIL") + " " + name + (("  -> " + extra) if extra and not cond else ""))
    if not cond:
        fails.append(name)

def no_shell(txt):
    return ("<!doctype" not in txt.lower()) and (txt.lower().count('class="tabbar"') == 0)

with TestClient(app) as c:
    # ---- login as the seeded demo user ----
    r = c.get("/login"); check("GET /login", r.status_code == 200 and "get you in" in r.text.lower())
    # Mirror the browser: echo the CSRF cookie as the X-CSRF-Token header.
    csrf = c.cookies.get("mtd_csrf", "")
    check("csrf cookie issued on GET", bool(csrf))
    HX = {"hx-request": "true", "x-csrf-token": csrf}
    r = c.post("/login", data={"email": "demo@example.com"}, headers=HX)
    check("POST /login (hx) fragment", r.status_code == 200 and no_shell(r.text), "shell leaked")
    m = re.search(r"your code is\s*<strong>(\d{6})</strong>", r.text)
    check("dev code shown", bool(m))
    code = m.group(1) if m else ""
    r = c.post("/verify", data={"email": "demo@example.com", "code": code}, headers=HX)
    check("POST /verify -> HX-Redirect", r.status_code == 200 and r.headers.get("HX-Redirect") in ("/capture", "/onboarding"))
    check("session cookie set", "mtd_session" in c.cookies)

    # ---- capture home + status line ----
    r = c.get("/capture"); check("GET /capture", r.status_code == 200 and "scan" in r.text.lower() and 'class="tabbar"' in r.text)
    r = c.get("/status/line", headers=HX)
    check("GET /status/line fragment", r.status_code == 200 and no_shell(r.text) and "statusline" in r.text)

    # ---- scan (gentle-failure path) -> confirm fragment ----
    r = c.post("/scan", data={"key": "", "failed": "1"}, headers=HX)
    check("POST /scan empty -> confirm fragment", r.status_code == 200 and no_shell(r.text) and ("couldn" in r.text.lower() or "amount" in r.text.lower()))

    # ---- sign + full scan happy path with a tiny real JPEG ----
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (40, 40), (200, 180, 160)).save(buf, "JPEG")
    r = c.post("/api/capture/sign", json={"content_type": "image/jpeg"}, headers=HX)
    check("POST /sign", r.status_code == 200 and "key" in r.json())
    key = r.json().get("key", "")
    r = c.put("/api/blob/" + key, content=buf.getvalue(), headers={**HX, "content-type": "image/jpeg"})
    check("PUT /api/blob", r.status_code == 200)
    r = c.post("/scan", data={"key": key}, headers=HX)
    check("POST /scan with image -> confirm prefilled", r.status_code == 200 and no_shell(r.text))

    # ---- save an expense -> saved fragment ----
    r = c.post("/api/entries", data={"key": key, "entry_type": "expense", "amount": "12.50", "entry_date": "2026-05-30", "vendor": "Test Garage"}, headers=HX)
    check("POST /api/entries -> saved", r.status_code == 200 and no_shell(r.text) and "saved" in r.text.lower())

    # ---- records ----
    r = c.get("/records"); check("GET /records", r.status_code == 200 and ("2026/27" in r.text or "2025/26" in r.text))

    # find a tax year id + an entry id + a month from DB
    from app.db import SessionLocal, Repo, get_user_by_email
    with SessionLocal() as s:
        u = get_user_by_email(s, "demo@example.com"); repo = Repo(s, u.id)
        tys = repo.tax_years(); ty = tys[0]
        entries = repo.list_entries(tax_year_id=ty.id)
        e = entries[0]; month = f"{e.entry_date.year}-{e.entry_date.month:02d}"
        cat = repo.categories(kind="expense")[0]
    r = c.get(f"/records/{ty.id}/{month}"); check("GET records month", r.status_code == 200)
    # inline edit fragment
    r = c.get(f"/records/entry/{e.id}/edit", headers=HX)
    check("GET entry edit fragment", r.status_code == 200 and no_shell(r.text))
    # edit a DATE (this is the json-serializer bug path) -> must not 500
    r = c.post(f"/records/entry/{e.id}", data={"amount": "20.00", "entry_date": "2026-05-15", "category_id": cat.id}, headers=HX)
    check("POST entry edit (date change, no 500)", r.status_code == 200 and no_shell(r.text))
    # delete + restore
    r = c.post(f"/records/entry/{e.id}/delete", headers={**HX, "hx-target": "closest .entry"})
    check("POST entry delete", r.status_code == 200)
    r = c.post(f"/records/entry/{e.id}/restore", headers=HX)
    check("POST entry restore", r.status_code == 200)

    # ---- categories create (the swap-fragment bug path) ----
    r = c.post("/categories", data={"label": "Toll roads", "sa103_code": "car_van_travel", "kind": "expense"}, headers=HX)
    check("POST /categories create -> fragment", r.status_code == 200 and no_shell(r.text) and "Toll roads" in r.text)

    # ---- account save (swap-fragment bug path) ----
    r = c.post("/account", data={"display_name": "Demo Instructor", "phone": "07700900000", "email_opt_in": "1"}, headers=HX)
    check("POST /account save -> fragment", r.status_code == 200 and no_shell(r.text) and "saved" in r.text.lower())

    # ---- status full page ----
    r = c.get("/status"); check("GET /status", r.status_code == 200 and "where you stand" in r.text.lower())

    # ---- export build + download ----
    r = c.post(f"/export/{ty.id}", headers=HX)
    check("POST /export build -> fragment", r.status_code == 200 and no_shell(r.text) and "ready" in r.text.lower())
    r = c.get(f"/export/file/{ty.id}")
    check("GET export file is zip", r.status_code == 200 and r.headers.get("content-type", "").startswith("application/zip") and len(r.content) > 500)

    # ---- cron reminders (idempotent) ----
    r = c.post("/api/cron/reminders"); check("POST cron", r.status_code == 200)

print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
