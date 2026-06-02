"""Jinja2 environment + render helpers shared by every route.

`render(...)` returns a full page (extends base.html); `partial(...)` returns
a fragment for HTMX swaps. Both stamp the CSRF cookie + token so the global
hx-headers on <body> can echo it back.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from pathlib import Path

from fastapi import Request
from starlette.templating import Jinja2Templates

from .config import settings
from .security import csrf_token_for, set_csrf_cookie
from .util import format_pounds

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ----- filters / globals -----
def _f_pounds(minor) -> str:
    return format_pounds(int(minor or 0))


def _f_signed(minor, kind="expense") -> str:
    return format_pounds(int(minor or 0), signed=True, kind=kind)


def _f_ddmonyyyy(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%-d %b %Y") if hasattr(d, "strftime") else str(d)
    return str(d or "")


def _f_dateinput(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d or "")


def _f_monthname(m) -> str:
    try:
        return calendar.month_name[int(m)]
    except (ValueError, IndexError, TypeError):
        return str(m)


# %-d isn't portable to Windows strftime; provide a safe variant.
def _f_nice_date(d) -> str:
    if isinstance(d, (date, datetime)):
        return f"{d.day} {calendar.month_abbr[d.month]} {d.year}"
    return str(d or "")


templates.env.filters["pounds"] = _f_pounds
templates.env.filters["signed"] = _f_signed
templates.env.filters["nicedate"] = _f_nice_date
templates.env.filters["dateinput"] = _f_dateinput
templates.env.filters["monthname"] = _f_monthname
templates.env.globals["format_pounds"] = format_pounds


def _media_url(key):
    if not key:
        return ""
    from .storage import get_storage  # lazy to avoid import cycles
    return get_storage().display_url(key)


templates.env.globals["media_url"] = _media_url


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _ctx(request: Request, user, extra: dict) -> dict:
    ctx = {
        "request": request,
        "current_user": user,
        "csrf_token": csrf_token_for(request),
        "settings": settings,
        "today": date.today(),
        # When true, base.html renders ONLY the screen block (a clean fragment
        # for HTMX swaps) instead of the full shell — so any base-extending
        # page can be safely swapped into #screen without nesting the chrome.
        "hx_request": is_htmx(request),
    }
    ctx.update(extra)
    return ctx


def render(request: Request, name: str, *, user=None, status_code: int = 200,
           **ctx):
    """Full-page render (template should extend base.html)."""
    token = csrf_token_for(request)
    context = _ctx(request, user, ctx)
    context["csrf_token"] = token  # meta MUST equal the cookie we set below
    resp = templates.TemplateResponse(request, name, context,
                                      status_code=status_code)
    set_csrf_cookie(resp, token)
    return resp


def partial(request: Request, name: str, *, user=None, status_code: int = 200,
            **ctx):
    """Fragment render for HTMX swaps (template should NOT extend base.html)."""
    return render(request, name, user=user, status_code=status_code, **ctx)
