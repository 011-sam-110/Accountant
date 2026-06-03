"""Auth + onboarding + account routes."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    AppUser, Category, Entry, OtpCode, Receipt, ReminderLog,
    Session as SessionRow, Student, TaxYear,
)
from ..emails import signin_email
from ..notify import send_email
from ..security import (
    clear_session, create_otp, current_user_optional, issue_session,
    otp_rate_limit, require_user, verify_otp,
)
from ..storage import get_storage
from ..templating import is_htmx, partial, render

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _looks_like_email(v: str) -> bool:
    return bool(v and _EMAIL_RE.match(v.strip()))


# ----------------------------------------------------------------------- login
@router.get("/login")
def login_page(request: Request, s: Session = Depends(get_session)):
    user = current_user_optional(request, s)
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", show_tabs=False)


@router.post("/login")
def login_post(request: Request, email: str = Form(""),
               s: Session = Depends(get_session)):
    email = email.strip().lower()
    if not _looks_like_email(email):
        return partial(request, "login.html",
                       error="Please enter a valid email address.",
                       step="email")
    ok, msg = otp_rate_limit(s, email)
    if not ok:
        return partial(request, "login.html", error=msg, step="email",
                       email_value=email)
    code = create_otp(s, email, ip=request.client.host if request.client else None)
    html, text = signin_email(code)
    send_email(email, "Your Tidy Books sign-in code", html, text)
    from ..config import settings  # local import avoids circular at module level
    return partial(request, "login.html", step="code", email=email,
                   dev_code=code if settings.dev_show_otp else None)


@router.post("/verify")
def verify_post(request: Request,
                email: str = Form(""), code: str = Form(""),
                s: Session = Depends(get_session)):
    email = email.strip().lower()
    user, status = verify_otp(s, email, code)
    if user is None:
        return partial(request, "login.html", step="code", email=email,
                       error=status)
    resp = Response(status_code=200)
    issue_session(s, resp, user, request.headers.get("user-agent"))
    dest = "/onboarding" if (status == "new" or not user.onboarded) else "/capture"
    resp.headers["HX-Redirect"] = dest
    return resp


# ---------------------------------------------------------------------- logout
@router.post("/logout")
def logout(request: Request, s: Session = Depends(get_session)):
    if is_htmx(request):
        resp = Response(status_code=200)
        clear_session(s, request, resp)
        resp.headers["HX-Redirect"] = "/login"
        return resp
    resp = RedirectResponse("/login", status_code=303)
    clear_session(s, request, resp)
    return resp


# ----------------------------------------------------------------- onboarding
@router.get("/onboarding")
def onboarding_page(request: Request, user: AppUser = Depends(require_user)):
    return render(request, "onboarding.html", user=user, show_tabs=False)


@router.post("/onboarding/done")
def onboarding_done(request: Request, user: AppUser = Depends(require_user),
                    s: Session = Depends(get_session)):
    user.onboarded = True
    s.flush()
    resp = Response(status_code=200)
    resp.headers["HX-Redirect"] = "/capture"
    return resp


# ----------------------------------------------------------------------- account
@router.get("/account")
def account_page(request: Request, user: AppUser = Depends(require_user)):
    return render(request, "account.html", user=user, show_tabs=True,
                  active_tab="more")


@router.post("/account")
def account_save(request: Request,
                 display_name: str = Form(""),
                 phone: str = Form(""),
                 email_opt_in: str | None = Form(None),
                 sms_opt_in: str | None = Form(None),
                 user: AppUser = Depends(require_user),
                 s: Session = Depends(get_session)):
    user.display_name = display_name.strip()
    user.phone = phone.strip() or None
    user.email_opt_in = email_opt_in is not None
    user.sms_opt_in = sms_opt_in is not None
    s.flush()
    return partial(request, "account.html", user=user, show_tabs=True,
                   active_tab="more", saved=True)


@router.post("/account/delete")
def account_delete(request: Request, user: AppUser = Depends(require_user),
                   s: Session = Depends(get_session)):
    # Remove blob storage for the user.
    try:
        get_storage().delete_prefix(user.id)
    except Exception:
        pass

    uid = user.id
    # Delete child rows (cascade-safe order).
    for model in (Receipt, Entry, ReminderLog, OtpCode, SessionRow, TaxYear,
                  Student, Category):
        s.query(model).filter_by(user_id=uid).delete(synchronize_session=False)
    s.flush()
    # Revoke current session cookie before deleting the user row.
    resp = Response(status_code=200)
    clear_session(s, request, resp)
    s.delete(user)
    s.flush()
    resp.headers["HX-Redirect"] = "/login"
    return resp
