"""Root gate, the 'More' hub, privacy page, health (foundation-owned)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import ON_VERCEL, settings
from ..db import get_session
from ..models import AppUser
from ..security import current_user_optional, require_user
from ..templating import render

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"ok": True, "env": settings.env, "storage": settings.effective_storage,
            "ocr": settings.effective_ocr, "email": settings.effective_email}


@router.get("/api/diag")
def diag(request: Request, user: AppUser = Depends(require_user)):
    """Browser-readable diagnostics (login required). Shows the effective
    providers and runs a LIVE OCR call so the real error (e.g. a Gemini 400/404
    with its message) is visible without digging through Vercel logs."""
    import io
    import sys

    from .. import ocr
    info = {
        "env": settings.env,
        "on_vercel": ON_VERCEL,
        "python": sys.version.split()[0],
        "storage": settings.effective_storage,
        "ocr_provider": settings.effective_ocr,
        "gemini_model": settings.gemini_model,
        "email_provider": settings.effective_email,
        "keys_present": {
            "gemini": bool(settings.gemini_api_key),
            "openai": bool(settings.openai_api_key),
            "anthropic": bool(settings.anthropic_api_key),
            "r2": bool(settings.r2_access_key_id and settings.r2_secret_access_key
                       and settings.r2_endpoint),
            "smtp": bool(settings.mail_server and settings.email_user),
        },
        "database": "sqlite" if settings.is_sqlite else "postgres",
    }
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (96, 96), (250, 248, 244)).save(buf, "JPEG")
        res = ocr.extract(buf.getvalue())
        info["ocr_test"] = {
            "ok": res.ok, "error": res.error,
            "amount_minor": res.amount_minor, "vendor": res.vendor,
            "category": res.suggested_category,
        }
    except Exception as exc:  # noqa: BLE001
        info["ocr_test"] = {"exception": repr(exc)}
    return info


@router.get("/")
def root(request: Request, s: Session = Depends(get_session)):
    user = current_user_optional(request, s)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not user.onboarded:
        return RedirectResponse("/onboarding", status_code=303)
    return RedirectResponse("/capture", status_code=303)


@router.get("/more")
def more(request: Request, user: AppUser = Depends(require_user)):
    return render(request, "more.html", user=user, active_tab="more", show_tabs=True)


@router.get("/privacy")
def privacy(request: Request, s: Session = Depends(get_session)):
    user = current_user_optional(request, s)
    return render(request, "privacy.html", user=user, show_tabs=False)
