"""Repeats — saved 'repeated payments' the instructor taps to log in one go.

Two ways a repeat appears:
  * Suggested — app/repeats.py reads regular pupils straight out of the income
    history and offers one-tap setup (the heavy lifting).
  * By hand — the "New repeat" form.

Logging a repeat always goes through Repo.create_entry, so nothing is ever
auto-posted: the ledger only ever holds entries the user actually tapped to
confirm. The post-log screen reuses capture's saved.html (with its Undo), and
archive reuses the OOB-toast undo pattern from records/categories.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import Repo, get_session
from ..models import AppUser
from ..repeats import detect_pupil_repeats
from ..security import require_user
from ..templating import partial, render
from ..util import parse_amount_to_minor

router = APIRouter()

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]


def _parse_weekday(raw: str) -> int | None:
    return int(raw) if raw.isdigit() and 0 <= int(raw) <= 6 else None


def _pupil_suggestions(repo: Repo):
    """Regular pupils detected from history, minus ones already set up/dismissed."""
    return detect_pupil_repeats(
        repo.list_entries(entry_type="income"),
        today=date.today(),
        students_by_id={st.id: st for st in repo.students()},  # active pupils only
        exclude_student_ids=repo.student_ids_with_repeat(),
        dismissed_student_ids=repo.dismissed_refs("student"),
    )


def _page_ctx(repo: Repo) -> dict:
    di = repo.default_category("income")
    de = repo.default_category("expense")
    return {
        "active_tab": "repeats", "show_tabs": True,
        "repeats": repo.list_repeats(),
        "suggestions": _pupil_suggestions(repo),
        "income_categories": repo.categories(kind="income", include_hidden=False),
        "expense_categories": repo.categories(kind="expense", include_hidden=False),
        "default_income_cat": di.id if di else "",
        "default_expense_cat": de.id if de else "",
        "students": repo.students(),
        "weekdays": list(enumerate(WEEKDAYS)),
    }


# ----------------------------------------------------------------- page ----
@router.get("/repeated")
def repeated_home(request: Request, user: AppUser = Depends(require_user),
                  s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    return render(request, "repeated.html", user=user, **_page_ctx(repo))


# ---------------------------------------------------------- manual create ----
@router.post("/repeated")
def create(request: Request,
           entry_type: str = Form(default="income"),
           amount: str = Form(default=""),
           student_name: str = Form(default=""),
           vendor: str = Form(default=""),
           income_category_id: str = Form(default=""),
           expense_category_id: str = Form(default=""),
           cadence: str = Form(default="weekly"),
           weekday: str = Form(default=""),
           default_paid: str = Form(default=""),
           user: AppUser = Depends(require_user),
           s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    kind = "income" if entry_type == "income" else "expense"

    try:
        amount_minor = parse_amount_to_minor(amount)
    except ValueError:
        return render(request, "repeated.html", user=user, **_page_ctx(repo),
                      open_form=True, form_type=kind,
                      form_amount=(amount or "").strip(),
                      form_error="Please pop in a usual amount to save this.")

    cid = (income_category_id if kind == "income" else expense_category_id) or None
    if cid and not repo.get_category(cid):
        cid = None
    if cid is None:
        d = repo.default_category(kind)
        cid = d.id if d else None

    sid = None
    if kind == "income" and student_name.strip():
        sid = repo.get_or_create_student(student_name).id
    paid = default_paid in ("on", "1", "true", "yes")
    # Only keep a free-text label when there's no student/vendor to name the row.
    label = "" if (sid or vendor.strip()) else student_name.strip()

    repo.create_repeat(
        entry_type=kind, label=label, amount_minor=amount_minor, category_id=cid,
        student_id=sid, vendor=(vendor.strip() or None),
        default_paid=(paid if kind == "income" else False),
        cadence=cadence, weekday=_parse_weekday(weekday))

    name = student_name.strip() or vendor.strip() or "Repeat"
    return render(request, "repeated.html", user=user, **_page_ctx(repo),
                  flash=f"Saved “{name}”. Tap ＋ Log to add it for today.")


# ------------------------------------------------------- from suggestion ----
@router.post("/repeated/from-suggestion")
def from_suggestion(request: Request, student_id: str = Form(default=""),
                    user: AppUser = Depends(require_user),
                    s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    sug = next((x for x in _pupil_suggestions(repo)
                if x.student_id == student_id), None)
    flash = None
    if sug:
        repo.create_repeat(
            entry_type="income", student_id=sug.student_id,
            amount_minor=sug.amount_minor, category_id=sug.category_id,
            cadence=sug.cadence, weekday=sug.weekday, default_paid=True)
        flash = f"Set up “{sug.name}”. Tap ＋ Log when you teach them."
    return render(request, "repeated.html", user=user, **_page_ctx(repo), flash=flash)


@router.post("/repeated/suggestion/dismiss")
def dismiss(request: Request, student_id: str = Form(default=""),
            user: AppUser = Depends(require_user),
            s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    if student_id:
        repo.dismiss_suggestion("student", student_id)
    return render(request, "repeated.html", user=user, **_page_ctx(repo))


# ------------------------------------------------------------ tap-to-log ----
@router.post("/repeated/{rid}/log")
def log_one(rid: str, request: Request, user: AppUser = Depends(require_user),
            s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    e = repo.log_repeat(rid, on_date=date.today())
    if e is None:
        r = repo.get_repeat(rid)
        flash = ("Add an amount to this repeat first."
                 if r else "That repeat has gone.")
        return render(request, "repeated.html", user=user, **_page_ctx(repo),
                      flash=flash)
    # Reuse capture's calm confirmation + Undo; just point "again" back here.
    return partial(request, "saved.html", user=user, entry=e,
                   again_href="/repeated", again_label="Log another repeat")


# -------------------------------------------------------- edit / archive ----
@router.get("/repeated/{rid}/edit")
def edit(rid: str, request: Request, user: AppUser = Depends(require_user),
         s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    r = repo.get_repeat(rid)
    if r is None:
        return Response("", status_code=200)
    return partial(request, "_repeat_edit.html", user=user, r=r,
                   categories=repo.categories(kind=r.entry_type, include_hidden=True),
                   weekdays=list(enumerate(WEEKDAYS)))


@router.get("/repeated/{rid}/row")
def row(rid: str, request: Request, user: AppUser = Depends(require_user),
        s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    r = repo.get_repeat(rid)
    if r is None:
        return Response("", status_code=200)
    return partial(request, "_repeat_row.html", user=user, r=r)


@router.post("/repeated/{rid}/archive")
def archive(rid: str, request: Request, user: AppUser = Depends(require_user),
            s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    r = repo.archive_repeat(rid)
    if r is None:
        return Response("", status_code=200)
    # Empty the row (primary swap) + drop an Undo toast out-of-band.
    return partial(request, "_repeat_toast.html", user=user, rid=rid,
                   message="Paused — find it under finished repeats.",
                   can_undo=True, oob=True)


@router.post("/repeated/{rid}/restore")
def restore(rid: str, request: Request, user: AppUser = Depends(require_user),
            s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    repo.restore_repeat(rid)
    return partial(request, "_repeat_toast.html", user=user, rid=rid,
                   message="Brought back.", can_undo=False, oob=False)


@router.post("/repeated/{rid}")
def update(rid: str, request: Request,
           amount: str = Form(default=""), category_id: str = Form(default=""),
           cadence: str = Form(default="weekly"), weekday: str = Form(default=""),
           default_paid: str = Form(default=""),
           user: AppUser = Depends(require_user),
           s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    r = repo.get_repeat(rid)
    if r is None:
        return Response("", status_code=200)
    fields: dict = {"cadence": cadence, "weekday": _parse_weekday(weekday)}
    try:
        fields["amount_minor"] = parse_amount_to_minor(amount)
    except ValueError:
        pass  # keep the old amount if it didn't parse
    if category_id and repo.get_category(category_id):
        fields["category_id"] = category_id
    if r.entry_type == "income":
        fields["default_paid"] = default_paid in ("on", "1", "true", "yes")
    updated = repo.update_repeat(rid, **fields)
    return partial(request, "_repeat_row.html", user=user, r=updated)
