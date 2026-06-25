"""EXPORT (accountant handover pack) routes.

GET  /export                    — list tax years + explain what the pack contains
POST /export/{tax_year_id}      — build the pack, store it, return a partial with
                                  a download link (HTMX swap)
GET  /export/file/{tax_year_id} — download the built zip (or rebuild on the fly)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import export as export_lib
from app.db import Repo, get_session
from app.models import AppUser
from app.security import require_user
from app.storage import get_storage
from app.templating import partial, render

log = logging.getLogger("mtd.export")
router = APIRouter()


def _pack_key(user_id: str, label: str) -> str:
    return f"{user_id}/exports/{label.replace('/', '-')}.zip"


def _get_or_build(repo: Repo, user: AppUser, ty) -> bytes:
    """Return existing stored zip, or build + store a fresh one."""
    storage = get_storage()
    key = _pack_key(user.id, ty.label)
    try:
        return storage.get_bytes(key)
    except Exception:
        pass
    data = export_lib.build_pack(repo, user, ty)
    try:
        storage.put_bytes(key, data, "application/zip")
    except Exception:
        log.warning("export: could not cache zip to storage")
    return data


# ----------------------------------------------------------------- GET /export

@router.get("/export")
def export_index(
    request: Request,
    user: AppUser = Depends(require_user),
    s: Session = Depends(get_session),
):
    repo = Repo(s, user.id)
    tax_years = repo.tax_years()

    # Attach quick totals to each year so the template can show them without
    # extra calls (we do it here to keep the template logic-free).
    year_rows = []
    for ty in tax_years:
        gross = repo.gross_income_minor(ty.id)
        expense = repo.expense_total_minor(ty.id)
        year_rows.append({
            "ty": ty,
            "gross_minor": gross,
            "expense_minor": expense,
            "net_minor": gross - expense,
        })

    return render(
        request, "export.html",
        user=user,
        active_tab="more",
        show_tabs=True,
        year_rows=year_rows,
    )


# ----------------------------------------------- POST /export/{tax_year_id}

@router.post("/export/{tax_year_id}")
def export_build(
    tax_year_id: str,
    request: Request,
    user: AppUser = Depends(require_user),
    s: Session = Depends(get_session),
):
    repo = Repo(s, user.id)
    ty = repo.get_tax_year(tax_year_id)
    if ty is None:
        return partial(
            request, "_export_result.html",
            user=user,
            error="We couldn't find that tax year — it may have been removed.",
            tax_year=None,
            size_kb=0,
            download_url="",
        )

    data = export_lib.build_pack(repo, user, ty)
    key = _pack_key(user.id, ty.label)
    try:
        get_storage().put_bytes(key, data, "application/zip")
    except Exception:
        log.warning("export: could not store zip for %s", ty.label)

    size_kb = max(1, round(len(data) / 1024))
    return partial(
        request, "_export_result.html",
        user=user,
        error=None,
        tax_year=ty,
        size_kb=size_kb,
        download_url=f"/export/file/{tax_year_id}",
    )


# --------------------------------------------- GET /export/file/{tax_year_id}

@router.get("/export/file/{tax_year_id}")
def export_download(
    tax_year_id: str,
    request: Request,
    user: AppUser = Depends(require_user),
    s: Session = Depends(get_session),
):
    repo = Repo(s, user.id)
    ty = repo.get_tax_year(tax_year_id)
    if ty is None:
        return Response("Tax year not found.", status_code=404)

    data = _get_or_build(repo, user, ty)
    filename = f"EasyBooks-{ty.label.replace('/', '-')}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
