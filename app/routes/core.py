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
from ..util import new_id

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"ok": True, "env": settings.env, "storage": settings.effective_storage,
            "ocr": settings.effective_ocr, "email": settings.effective_email}


def _redact_secrets(text: str) -> str:
    """Strip configured secret values from text before it leaves in a response
    (a boto3 signature error can echo the access key id back at you)."""
    for secret in (settings.r2_secret_access_key, settings.r2_access_key_id):
        if secret:
            text = text.replace(secret, "REDACTED")
    return text


def _r2_selftest() -> dict:
    """Write → read-back → delete a tiny object against the configured R2
    bucket, server-side. Skipping the browser isolates a credential/bucket
    problem from a CORS one, and surfaces the exact boto3 error — e.g.
    AccessDenied (token not scoped to this bucket) or NoSuchBucket (typo)."""
    from ..storage import get_storage
    key = f"_diag/selftest-{new_id()}.txt"
    try:
        st = get_storage()
        st.put_bytes(key, b"tidybooks-selftest", "text/plain")
        read_back = st.exists(key)
        try:
            st.delete_prefix(key)  # best-effort cleanup; don't fail the test on it
            cleaned = True
        except Exception:  # noqa: BLE001
            cleaned = False
        return {"ok": True, "wrote_and_read_back": read_back, "cleaned_up": cleaned}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _redact_secrets(repr(exc))}


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
                       and settings.r2_endpoint_url),
            "smtp": bool(settings.mail_server and settings.email_user),
        },
        "database": "sqlite" if settings.is_sqlite else "postgres",
    }
    # Storage detail: show exactly what R2 is pointed at (no secrets) so a
    # mis-set bucket/endpoint or vars-not-reaching-Vercel is obvious, then run
    # a live server-side round-trip when R2 is the selected backend.
    info["r2"] = {
        "selected": settings.effective_storage == "r2",
        "access_key_id_set": bool(settings.r2_access_key_id),
        "secret_set": bool(settings.r2_secret_access_key),
        "endpoint": settings.r2_endpoint_url or "(not set)",
        "bucket": settings.r2_bucket or "(not set)",
    }
    if settings.effective_storage == "r2":
        info["r2_test"] = _r2_selftest()
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
