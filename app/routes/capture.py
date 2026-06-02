"""Capture + OCR — the heart of the app (scan a receipt -> confirm -> save).

Flow (all swaps target #screen, all mutations are HTMX so CSRF rides along):
  GET  /capture            home: big Scan button + manual/income links + recent
  POST /api/capture/sign   hand the browser a presigned PUT + the storage key
  POST /scan               read the uploaded image, render the pre-filled confirm
  POST /api/entries        save an expense (manual or from a scan); link receipt
  POST /capture/income     save an income payment (kept separate from /api/entries)
  GET  /capture/manual     type an expense by hand
  GET  /capture/income     quick-add a payment in

Golden rule mirrors ocr.extract(): the scan path never dead-ends. A failed read
still reaches the confirm screen (just empty) so the user can type the details.
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Body, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import ocr
from ..config import settings
from ..db import Repo, get_session
from ..models import AppUser
from ..ocr import OCRResult
from ..security import require_user
from ..storage import get_storage
from ..templating import partial, render
from ..util import from_minor, new_id, parse_amount_to_minor, parse_uk_date

router = APIRouter()
log = logging.getLogger("mtd.capture")


def _amount_str(amount_minor: int | None) -> str:
    """Pre-fill the number pad: pennies -> a bare '61.40' (no symbol)."""
    if amount_minor is None:
        return ""
    return f"{from_minor(amount_minor):.2f}"


# ===================================================================== home ==
@router.get("/capture")
def capture_home(request: Request, user: AppUser = Depends(require_user),
                 s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    return render(request, "capture.html", user=user,
                  active_tab="capture", show_tabs=True,
                  recent=repo.recent_entries(5))


# ============================================================ upload signing ==
@router.post("/api/capture/sign")
def sign_upload(request: Request, payload: dict = Body(default={}),
                user: AppUser = Depends(require_user)):
    content_type = payload.get("content_type") or "image/jpeg"
    today = date.today()
    key = f"{user.id}/{today:%Y/%m}/{new_id()}.jpg"
    return {"key": key, **get_storage().upload_url(key, content_type)}


# ============================================================ scan -> confirm ==
@router.post("/scan")
def scan(request: Request, key: str = Form(default=""), failed: str = Form(default=""),
         user: AppUser = Depends(require_user), s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    if key:
        try:
            image_bytes = get_storage().get_bytes(key)
            res = ocr.extract(image_bytes)
        except Exception as exc:
            # Couldn't even fetch the bytes — fall back to the gentle empty path.
            # On Vercel without R2 this is expected: the upload PUT and this /scan
            # request can land on different ephemeral instances, so /tmp differs.
            log.warning("scan: could not read %s from %s storage: %r — "
                        "set R2_* for durable shared storage on serverless.",
                        key, settings.effective_storage, exc)
            res = OCRResult(ok=False,
                            confidence={k: "missing" for k in
                                        ("amount", "date", "vendor", "category")})
    else:
        # No key (compress/upload failed client-side): empty, gentle-failure.
        log.info("scan: no key supplied (failed=%s)", failed)
        res = OCRResult(ok=False,
                        confidence={k: "missing" for k in
                                    ("amount", "date", "vendor", "category")})
    log.info("scan outcome: storage=%s ocr=%s ok=%s empty=%s err=%s",
             settings.effective_storage, settings.effective_ocr,
             res.ok, res.empty, res.error)

    cats = repo.categories(kind="expense", include_hidden=False)
    selected = next((c for c in cats if c.sa103_code == res.suggested_category), None)
    if selected is None:
        selected = repo.default_category("expense")
    selected_id = selected.id if selected else ""

    read_ok = bool(key) and res.ok and not res.empty
    return partial(request, "confirm.html", user=user,
                   key=key,
                   show_thumb=bool(key),
                   read_ok=read_ok,
                   amount_value=_amount_str(res.amount_minor),
                   entry_date=res.date or date.today(),
                   vendor=res.vendor or "",
                   categories=cats,
                   selected_category_id=selected_id,
                   confidence=res.confidence)


# ============================================================ save an expense ==
@router.post("/api/entries")
def create_entry(request: Request,
                 key: str = Form(default=""),
                 entry_type: str = Form(default="expense"),
                 entry_date: str = Form(default=""),
                 amount: str = Form(default=""),
                 category_id: str = Form(default=""),
                 vendor: str = Form(default=""),
                 notes: str = Form(default=""),
                 student_id: str = Form(default=""),
                 is_paid: str = Form(default=""),
                 user: AppUser = Depends(require_user),
                 s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    cats = repo.categories(kind="expense", include_hidden=False)

    # Amount is required — re-render confirm with an amber nudge if unparseable.
    try:
        amount_minor = parse_amount_to_minor(amount)
    except ValueError:
        return partial(request, "confirm.html", user=user,
                       key=key,
                       show_thumb=bool(key),
                       read_ok=False,
                       amount_value=(amount or "").strip(),
                       entry_date=parse_uk_date(entry_date, default=date.today()),
                       vendor=vendor,
                       categories=cats,
                       selected_category_id=category_id,
                       confidence={"amount": "low", "date": "high",
                                   "vendor": "high", "category": "high"},
                       amount_error="Please pop the amount in to save this.",
                       status_code=200)

    when = parse_uk_date(entry_date, default=date.today())

    cid = category_id or None
    if cid and not repo.get_category(cid):
        cid = None
    if cid is None:
        default = repo.default_category("expense")
        cid = default.id if default else None

    paid = None
    if entry_type == "income":
        paid = is_paid in ("on", "1", "true", "yes")

    e = repo.create_entry(
        entry_type=(entry_type or "expense"),
        entry_date=when,
        amount_minor=amount_minor,
        category_id=cid,
        vendor=(vendor.strip() or None),
        notes=(notes.strip() or None),
        student_id=(student_id or None),
        is_paid=paid,
    )
    if key:
        repo.create_receipt(storage_key=key, entry_id=e.id)

    return partial(request, "saved.html", user=user, entry=e)


# ============================================================== manual entry ==
@router.get("/capture/manual")
def manual(request: Request, user: AppUser = Depends(require_user),
           s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    default = repo.default_category("expense")
    return render(request, "manual.html", user=user,
                  active_tab="capture", show_tabs=True,
                  categories=repo.categories(kind="expense", include_hidden=False),
                  selected_category_id=(default.id if default else ""),
                  entry_date=date.today())


# ================================================================ income add ==
@router.get("/capture/income")
def income(request: Request, user: AppUser = Depends(require_user),
           s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    default = repo.default_category("income")
    return render(request, "income.html", user=user,
                  active_tab="capture", show_tabs=True,
                  categories=repo.categories(kind="income", include_hidden=False),
                  selected_category_id=(default.id if default else ""),
                  students=repo.students(),
                  entry_date=date.today())


@router.post("/capture/income")
def create_income(request: Request,
                  amount: str = Form(default=""),
                  entry_date: str = Form(default=""),
                  student_name: str = Form(default=""),
                  category_id: str = Form(default=""),
                  is_paid: str = Form(default=""),
                  notes: str = Form(default=""),
                  user: AppUser = Depends(require_user),
                  s: Session = Depends(get_session)):
    repo = Repo(s, user.id)

    try:
        amount_minor = parse_amount_to_minor(amount)
    except ValueError:
        default = repo.default_category("income")
        return render(request, "income.html", user=user,
                      active_tab="capture", show_tabs=True,
                      categories=repo.categories(kind="income", include_hidden=False),
                      selected_category_id=(category_id or
                                            (default.id if default else "")),
                      students=repo.students(),
                      entry_date=parse_uk_date(entry_date, default=date.today()),
                      amount_value=(amount or "").strip(),
                      student_name=student_name,
                      amount_error="Please pop the amount in to save this.")

    when = parse_uk_date(entry_date, default=date.today())

    cid = category_id or None
    if cid and not repo.get_category(cid):
        cid = None
    if cid is None:
        default = repo.default_category("income")
        cid = default.id if default else None

    sid = repo.get_or_create_student(student_name).id if student_name.strip() else None
    paid = is_paid in ("on", "1", "true", "yes")

    e = repo.create_entry(
        entry_type="income",
        entry_date=when,
        amount_minor=amount_minor,
        category_id=cid,
        student_id=sid,
        is_paid=paid,
        notes=(notes.strip() or None),
    )
    return partial(request, "saved.html", user=user, entry=e)
