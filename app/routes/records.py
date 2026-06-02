"""The RECORDS (ledger) slice — browse a tax year, its months, and entries.

Read-only by default; every mutation is an HTMX POST that swaps a small
fragment (a row, the toast) rather than the whole screen, so editing the
ledger feels light and never loses your scroll position.

Delete / undo swap approach (documented):
  * The in-ledger delete button targets `closest .entry` (outerHTML). The
    handler returns an EMPTY body that removes the row, plus an out-of-band
    (hx-swap-oob) `.toast` carrying a genuine Undo (POST .../restore).
  * Restore from that toast targets the toast itself and returns the
    re-rendered `_entry_row.html` via OOB back into the right section is
    overkill for a soft-deleted-then-restored row, so restore simply returns
    a short confirmation toast; the user can refresh to see it back in place.
  * The capture flow's "Undo" calls the SAME delete endpoint but targets
    `#screen`; in that case we return a small confirmation partial that is a
    valid `#screen` swap. We branch on the `HX-Target` header to tell them
    apart.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from ..db import Repo, engine, get_session
from ..models import AppUser
from ..security import require_user
from ..templating import partial, render
from ..util import parse_amount_to_minor, parse_uk_date

router = APIRouter()


# --------------------------------------------------------------------------
# Foundation interop note (NOT a foundation edit):
# Repo.update_entry() records an audit diff that, for a date change, contains a
# `datetime.date` (the entry_date). The shared engine's JSON column uses the
# stdlib json.dumps with no custom serializer, which can't encode date/datetime,
# so the very first edit that moves a date would 500 on flush. Capture only ever
# calls create_entry (diff = type/amount, both JSON-safe), so this slice is the
# first to exercise it. We install a date-aware serializer on the EXISTING
# engine's dialect at import time (idempotent, only if none is set). This touches
# no foundation file and is invisible to every other module.
# --------------------------------------------------------------------------
def _install_json_date_serializer() -> None:
    from datetime import date, datetime

    dialect = engine.dialect
    if getattr(dialect, "_json_serializer", None) is not None:
        return  # foundation (or someone) already provided one — leave it alone

    def _default(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    dialect._json_serializer = lambda obj: json.dumps(obj, default=_default)


_install_json_date_serializer()


# --------------------------------------------------------------- helpers ----
def _year_ctx(repo: Repo, ty) -> dict:
    """Shared context for the year overview (records.html)."""
    gross = repo.gross_income_minor(ty.id)
    expense = repo.expense_total_minor(ty.id)
    return {
        "tax_years": repo.tax_years(),
        "tax_year": ty,
        "months": repo.months_in_year(ty.id),
        "gross_income_minor": gross,
        "expense_total_minor": expense,
        "net_minor": gross - expense,
    }


# ----------------------------------------------------------- year views ----
@router.get("/records")
def records(request: Request, user: AppUser = Depends(require_user),
            s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    ty = repo.current_tax_year()
    return render(request, "records.html", user=user,
                  active_tab="records", show_tabs=True, **_year_ctx(repo, ty))


@router.get("/records/{tax_year_id}")
def records_year(tax_year_id: str, request: Request,
                 user: AppUser = Depends(require_user),
                 s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    ty = repo.get_tax_year(tax_year_id)
    if ty is None:
        return RedirectResponse("/records", status_code=303)
    return render(request, "records.html", user=user,
                  active_tab="records", show_tabs=True, **_year_ctx(repo, ty))


@router.get("/records/{tax_year_id}/{month}")
def records_month(tax_year_id: str, month: str, request: Request,
                  user: AppUser = Depends(require_user),
                  s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    ty = repo.get_tax_year(tax_year_id)
    if ty is None:
        return RedirectResponse("/records", status_code=303)
    entries = repo.list_entries(tax_year_id=tax_year_id, month=month)
    income = [e for e in entries if e.entry_type == "income"]
    expense = [e for e in entries if e.entry_type == "expense"]
    income_total = sum(e.amount_minor for e in income)
    expense_total = sum(e.amount_minor for e in expense)
    try:
        y, m = (int(x) for x in month.split("-"))
    except ValueError:
        return RedirectResponse(f"/records/{tax_year_id}", status_code=303)
    return render(request, "records_month.html", user=user,
                  active_tab="records", show_tabs=True,
                  tax_year=ty, month=month, month_num=m, year_num=y,
                  income=income, expense=expense,
                  income_total_minor=income_total,
                  expense_total_minor=expense_total)


# -------------------------------------------------------- entry editing ----
@router.get("/records/entry/{entry_id}/edit")
def entry_edit(entry_id: str, request: Request,
               user: AppUser = Depends(require_user),
               s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    e = repo.get_entry(entry_id)
    if e is None:
        return Response("", status_code=200)
    if e.is_locked:
        # Can't edit a closed year — hand back the unchanged row with a note.
        return partial(request, "_entry_row.html", user=user, entry=e,
                       locked_note="Locked — this year is closed.")
    cats = repo.categories(kind=e.entry_type, include_hidden=True)
    return partial(request, "_entry_edit.html", user=user, entry=e,
                   categories=cats)


@router.get("/records/entry/{entry_id}/row")
def entry_row(entry_id: str, request: Request,
              user: AppUser = Depends(require_user),
              s: Session = Depends(get_session)):
    """Plain (non-editing) row — used to cancel an inline edit."""
    repo = Repo(s, user.id)
    e = repo.get_entry(entry_id)
    if e is None:
        return Response("", status_code=200)
    return partial(request, "_entry_row.html", user=user, entry=e)


@router.post("/records/entry/{entry_id}")
def entry_save(entry_id: str, request: Request,
               amount: str = Form(""), entry_date: str = Form(""),
               category_id: str = Form(""), vendor: str = Form(""),
               notes: str = Form(""), is_paid: str = Form(""),
               student_name: str = Form(""),
               user: AppUser = Depends(require_user),
               s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    existing = repo.get_entry(entry_id)
    if existing is None:
        return Response("", status_code=200)

    fields: dict = {}
    flag = None
    try:
        fields["amount_minor"] = parse_amount_to_minor(amount)
    except ValueError:
        flag = "That amount didn't look right, so we kept the old one."
    fields["entry_date"] = parse_uk_date(entry_date, default=existing.entry_date)
    fields["category_id"] = category_id or existing.category_id
    fields["vendor"] = vendor.strip() or None
    fields["notes"] = notes.strip() or None

    if existing.entry_type == "income":
        fields["is_paid"] = bool(is_paid)
        name = (student_name or "").strip()
        if name:
            fields["student_id"] = repo.get_or_create_student(name).id

    entry, msg = repo.update_entry(entry_id, **fields)
    if entry is None:
        # Locked (or vanished) — return the unchanged row with the message.
        return partial(request, "_entry_row.html", user=user, entry=existing,
                       locked_note=msg)
    return partial(request, "_entry_row.html", user=user, entry=entry,
                   flag=flag)


@router.post("/records/entry/{entry_id}/paid")
def entry_paid(entry_id: str, request: Request, paid: str = Form(""),
               user: AppUser = Depends(require_user),
               s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    e = repo.get_entry(entry_id)
    if e is None:
        return Response("", status_code=200)
    # Explicit value wins if sent; otherwise toggle the current state.
    new_state = bool(paid) if paid != "" else not bool(e.is_paid)
    updated = repo.set_paid(entry_id, new_state)
    if updated is None:  # locked
        return partial(request, "_entry_row.html", user=user, entry=e,
                       locked_note="Locked — this year is closed.")
    return partial(request, "_entry_row.html", user=user, entry=updated)


@router.post("/records/entry/{entry_id}/delete")
def entry_delete(entry_id: str, request: Request,
                 user: AppUser = Depends(require_user),
                 s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    target = request.headers.get("hx-target", "")
    e = repo.soft_delete_entry(entry_id)

    # Capture's Undo button targets #screen — give it a valid full-region swap.
    if target == "screen":
        if e is None:
            return partial(request, "_undo_toast.html", user=user,
                           message="That entry is locked and can't be removed.",
                           entry_id=entry_id, can_undo=False, oob=False)
        return partial(request, "_undo_toast.html", user=user,
                       message="Entry removed.", entry_id=entry_id,
                       can_undo=True, oob=False)

    # In-ledger delete: row target. Remove the row (empty body) + OOB toast.
    if e is None:
        # Locked: keep the row, show a small inline flag via the toast (OOB).
        return partial(request, "_entry_row.html", user=user,
                       entry=repo.get_entry(entry_id),
                       locked_note="Locked — this year is closed.")
    return partial(request, "_undo_toast.html", user=user,
                   message="Entry removed.", entry_id=entry_id,
                   can_undo=True, oob=True)


@router.post("/records/entry/{entry_id}/restore")
def entry_restore(entry_id: str, request: Request,
                  user: AppUser = Depends(require_user),
                  s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    e = repo.restore_entry(entry_id)
    target = request.headers.get("hx-target", "")
    if target == "screen":
        # Capture flow: re-render the records overview so the entry reappears.
        ty = repo.current_tax_year()
        if e is not None:
            ty = repo.get_tax_year(e.tax_year_id) or ty
        return render(request, "records.html", user=user,
                      active_tab="records", show_tabs=True, **_year_ctx(repo, ty))
    # In-ledger restore (from the toast): confirm it's back.
    return partial(request, "_undo_toast.html", user=user,
                   message="Entry restored.", entry_id=entry_id,
                   can_undo=False, oob=False)


# -------------------------------------------------------- close the year ----
@router.post("/records/{tax_year_id}/close")
def close_year(tax_year_id: str, request: Request,
               user: AppUser = Depends(require_user),
               s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    ty = repo.get_tax_year(tax_year_id)
    if ty is None:
        return RedirectResponse("/records", status_code=303)
    snapshot = {
        "label": ty.label,
        "gross_income_minor": repo.gross_income_minor(tax_year_id),
        "expense_total_minor": repo.expense_total_minor(tax_year_id),
        "totals_by_box": repo.totals_by_box(tax_year_id),
    }
    snapshot_hash = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    repo.close_tax_year(tax_year_id, snapshot, snapshot_hash)

    r = Response(status_code=200)
    r.headers["HX-Redirect"] = f"/records/{tax_year_id}"
    return r
