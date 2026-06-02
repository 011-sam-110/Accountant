"""Auth primitives: sessions, CSRF, identity dependency, OTP.

- Sessions: signed (itsdangerous) HttpOnly cookie carrying {uid, sid}; a
  `session` row backs revocation. Long rolling expiry — a nervous user must
  never feel locked out.
- Identity: `current_user_optional` / `require_user` are the ONLY source of
  caller identity (plan §8). Routes build a Repo(session, user.id) from it.
- OTP: 6-digit, hashed at rest, short expiry, rate + attempt limited, no
  account enumeration (auto-creates the user on first successful code).
"""
from __future__ import annotations

import hmac
import secrets
from datetime import timedelta
from hashlib import sha256

from fastapi import Depends, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import (create_session_row, create_user, get_session, get_session_row,
                 get_user_by_email, get_user_by_id)
from .models import AppUser, OtpCode
from .util import utcnow

SESSION_COOKIE = "mtd_session"
CSRF_COOKIE = "mtd_csrf"
CSRF_HEADER = "x-csrf-token"

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="mtd-session")


class NotAuthenticated(Exception):
    """Raised by require_user; handled in api/index.py -> redirect to /login."""


# ----------------------------------------------------------- sessions ----
def issue_session(s: Session, response, user: AppUser, user_agent: str | None = None):
    row = create_session_row(
        s, user.id, expires_at=utcnow() + timedelta(days=settings.session_days),
        user_agent=(user_agent or "")[:255])
    token = _serializer.dumps({"uid": user.id, "sid": row.id})
    response.set_cookie(
        SESSION_COOKIE, token, max_age=settings.session_days * 86400,
        httponly=True, samesite="lax", secure=not settings.is_dev, path="/")


def clear_session(s: Session, request: Request, response):
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        try:
            data = _serializer.loads(raw, max_age=settings.session_days * 86400)
            row = get_session_row(s, data.get("sid"))
            if row:
                row.revoked_at = utcnow()
        except (BadSignature, SignatureExpired):
            pass
    response.delete_cookie(SESSION_COOKIE, path="/")


def current_user_optional(request: Request,
                          s: Session = Depends(get_session)) -> AppUser | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        data = _serializer.loads(raw, max_age=settings.session_days * 86400)
    except (BadSignature, SignatureExpired):
        return None
    row = get_session_row(s, data.get("sid", ""))
    if not row or row.revoked_at is not None or row.expires_at < utcnow():
        return None
    return get_user_by_id(s, data.get("uid", ""))


def require_user(request: Request,
                 s: Session = Depends(get_session)) -> AppUser:
    user = current_user_optional(request, s)
    if user is None:
        raise NotAuthenticated()
    return user


# --------------------------------------------------------------- CSRF ----
def make_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_token_for(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or make_csrf_token()


def set_csrf_cookie(response, token: str):
    response.set_cookie(CSRF_COOKIE, token, samesite="lax",
                        secure=not settings.is_dev, path="/")


def csrf_ok(request: Request) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    return bool(cookie and header and hmac.compare_digest(cookie, header))


# ---------------------------------------------------------------- OTP ----
def _hash_otp(email: str, code: str) -> str:
    msg = f"{email.strip().lower()}:{code}".encode()
    return hmac.new(settings.secret_key.encode(), msg, sha256).hexdigest()


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def otp_rate_limit(s: Session, email: str) -> tuple[bool, str]:
    """~1/60s and ~5/hour per email (Postgres-backed; serverless-safe)."""
    email = email.strip().lower()
    last = s.scalar(select(func.max(OtpCode.created_at)).where(OtpCode.email == email))
    if last and (utcnow() - last).total_seconds() < 55:
        return False, "We just sent a code — give it a few seconds, then check again."
    hour_ago = utcnow() - timedelta(hours=1)
    n = s.scalar(select(func.count()).select_from(OtpCode).where(
        OtpCode.email == email, OtpCode.created_at >= hour_ago)) or 0
    if n >= 5:
        return False, "Too many codes requested. Please try again in a little while."
    return True, ""


def create_otp(s: Session, email: str, ip: str | None = None) -> str:
    """Invalidate prior unused codes, create a new one, return the plain code."""
    email = email.strip().lower()
    for old in s.scalars(select(OtpCode).where(
            OtpCode.email == email, OtpCode.used_at.is_(None))).all():
        old.used_at = utcnow()
    code = generate_otp()
    s.add(OtpCode(email=email, code_hash=_hash_otp(email, code),
                  expires_at=utcnow() + timedelta(minutes=settings.otp_ttl_minutes),
                  ip=(ip or "")[:64]))
    s.flush()
    return code


def verify_otp(s: Session, email: str, code: str) -> tuple[AppUser | None, str]:
    """Verify a code. On success, auto-create the user (passwordless signup).

    Generic failure messages + identical paths => no account enumeration.
    """
    email = email.strip().lower()
    code = (code or "").strip()
    rec = s.scalar(select(OtpCode).where(
        OtpCode.email == email, OtpCode.used_at.is_(None)
    ).order_by(OtpCode.created_at.desc()))
    if not rec:
        return None, "That code has expired. Please request a new one."
    if rec.expires_at < utcnow():
        return None, "That code has expired. Please request a new one."
    if rec.attempts >= 5:
        rec.used_at = utcnow()
        return None, "Too many attempts. Please request a new code."
    rec.attempts += 1
    if not hmac.compare_digest(rec.code_hash, _hash_otp(email, code)):
        s.flush()
        return None, "That code wasn't right. Please check and try again."
    rec.used_at = utcnow()
    user = get_user_by_email(s, email)
    is_new = user is None
    if is_new:
        user = create_user(s, email)
    s.flush()
    return user, ("new" if is_new else "ok")
