"""Root gate, the 'More' hub, privacy page, health (foundation-owned)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from ..models import AppUser
from ..security import current_user_optional, require_user
from ..templating import render

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"ok": True, "env": settings.env, "storage": settings.effective_storage,
            "ocr": settings.effective_ocr, "email": settings.effective_email}


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
