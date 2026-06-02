"""Single ASGI entrypoint. On Vercel this is the one Python Function; locally
run with `uvicorn api.index:app --reload`.

Wires middleware (CSRF, security headers), the auth-redirect handler, static
assets, and every route module. Routers are included defensively so the app
still boots if a module is missing (useful while the build is in progress).
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure the project root (parent of api/) is importable. Vercel's function
# runtime may not place it on sys.path, which would break `import app`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, settings
from app.db import SessionLocal, Repo, create_user, get_user_by_email, init_db
from app.security import NotAuthenticated, csrf_ok
from app.templating import render
from app.util import to_minor

log = logging.getLogger("mtd")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Tidy Books — MTD app", docs_url=None, redoc_url=None)

# Locally we serve /public ourselves. On Vercel it's CDN-served by @vercel/static
# and is NOT in the function bundle, so only mount when the dir exists (StaticFiles
# raises on a missing dir, and the FS is read-only there — never mkdir at import).
PUBLIC_DIR = ROOT / "public"
if PUBLIC_DIR.is_dir():
    app.mount("/public", StaticFiles(directory=str(PUBLIC_DIR)), name="public")

CSRF_EXEMPT_PREFIXES = ("/api/cron",)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def security_and_csrf(request: Request, call_next):
    # CSRF: double-submit cookie vs header on every state-changing request.
    if request.method not in SAFE_METHODS and \
            not request.url.path.startswith(CSRF_EXEMPT_PREFIXES):
        if not csrf_ok(request):
            return Response("CSRF check failed. Please refresh and try again.",
                            status_code=403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy",
                            "geolocation=(), microphone=(), camera=(self)")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "  # Alpine needs eval
        "connect-src 'self'; base-uri 'self'; form-action 'self'")
    return resp


@app.exception_handler(NotAuthenticated)
async def not_authenticated(request: Request, exc: NotAuthenticated):
    # HTMX requests can't follow a 303 body-swap; use HX-Redirect.
    if request.headers.get("hx-request") == "true":
        r = Response(status_code=200)
        r.headers["HX-Redirect"] = "/login"
        return r
    return RedirectResponse("/login", status_code=303)


# --------------------------------------------------------- router wiring ----
# (module, friendly name). Foundation modules first, then swarm features.
ROUTE_MODULES = [
    "app.routes.core", "app.routes.media", "app.routes.auth",
    "app.routes.capture", "app.routes.records", "app.routes.repeated",
    "app.routes.categories", "app.routes.status", "app.routes.export",
    "app.routes.cron",
]
for mod_name in ROUTE_MODULES:
    try:
        mod = importlib.import_module(mod_name)
        app.include_router(mod.router)
        log.info("mounted %s", mod_name)
    except ModuleNotFoundError as exc:
        if exc.name == mod_name:  # module itself absent (not yet built)
            log.warning("skipped %s (not built yet)", mod_name)
        else:  # a dependency of the module is missing — log, don't crash the app
            log.exception("FAILED importing %s (missing: %s)", mod_name, exc.name)
    except AttributeError:
        log.warning("skipped %s (no `router`)", mod_name)
    except Exception:  # any other import-time error: log the traceback, keep serving
        log.exception("FAILED mounting %s", mod_name)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.headers.get("accept", "").find("text/html") >= 0:
        try:
            return render(request, "notfound.html", show_tabs=False, status_code=404)
        except Exception:
            pass
    return Response("Not found", status_code=404)


# ------------------------------------------------------------- startup ----
def _startup():
    init_db()
    log.info("storage=%s ocr=%s email=%s sms=%s db=%s",
             settings.effective_storage, settings.effective_ocr,
             settings.effective_email, settings.effective_sms,
             "sqlite" if settings.is_sqlite else "postgres")
    if settings.seed_demo_users:
        _seed_demo()


def _seed_demo():
    """Idempotent demo data so Records/Status/Export have content on first run."""
    with SessionLocal() as s:
        if get_user_by_email(s, "demo@example.com"):
            return
        user = create_user(s, "demo@example.com", "Demo Instructor")
        user.onboarded = True
        repo = Repo(s, user.id)
        cats = {c.sa103_code: c for c in repo.categories()}

        def add(kind, code, amount, when, vendor=None, student=None, paid=None, note=None):
            sid = repo.get_or_create_student(student).id if student else None
            repo.create_entry(entry_type=kind, entry_date=when,
                              amount_minor=to_minor(amount),
                              category_id=cats[code].id, vendor=vendor,
                              student_id=sid, is_paid=paid, notes=note)

        ty = date(2026, 4, 6)        # current year 2026/27
        py = date(2025, 6, 15)       # prior year 2025/26
        # --- current year: lessons (income) + costs (expense) ---
        for i, name in enumerate(["Aisha", "Tom", "Priya", "Jordan", "Mia"]):
            add("income", "income_lesson", 34.00 + i, date(2026, 5, 6 + i*3),
                student=name, paid=(i % 3 != 0))
        add("income", "income_lesson", 170.00, date(2026, 5, 20), student="Block of 5", paid=True)
        add("expense", "car_van_travel", 61.40, date(2026, 5, 8), vendor="Shell")
        add("expense", "car_van_travel", 58.10, date(2026, 5, 22), vendor="BP")
        add("expense", "vehicle_costs", 189.00, date(2026, 5, 14), vendor="Kwik Fit", note="Service")
        add("expense", "phone_internet", 32.00, date(2026, 5, 2), vendor="Vodafone")
        add("expense", "advertising_marketing", 45.00, date(2026, 5, 11), vendor="Vistaprint")
        # --- regular weekly pupils, RELATIVE to today, so the Repeats screen has
        #     a real recurring schedule to detect (~8 weeks of history each) ---
        from datetime import timedelta
        today = date.today()
        for name, wd, amt in [("Sarah", 0, 30.00), ("Liam", 1, 34.00),
                              ("Chloe", 3, 36.00), ("Mo", 4, 28.00)]:
            last = today - timedelta(days=(today.weekday() - wd) % 7)
            for w in range(8):
                add("income", "income_lesson", amt, last - timedelta(weeks=w),
                    student=name, paid=(w != 0))
        # --- prior year 2025/26: a fuller year, nudging toward the line ---
        for m in range(6, 13):
            add("income", "income_lesson", 3200.00, date(2025, m, 12), paid=True)
        add("income", "income_lesson", 3400.00, date(2026, 1, 12), paid=True)
        add("expense", "car_van_travel", 240.00, py, vendor="Shell")
        add("expense", "vehicle_costs", 520.00, date(2025, 9, 3), vendor="Halfords")
        s.commit()
        log.info("seeded demo user demo@example.com")


# Initialise the DB at import (cold start) so it doesn't depend on the platform
# honouring ASGI lifespan; guard it so any failure is logged (and visible in the
# Vercel runtime logs) instead of crashing the whole function opaquely.
try:
    _startup()
except Exception:
    log.exception("startup (init_db/seed) failed")
