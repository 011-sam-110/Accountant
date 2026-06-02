"""The STATUS slice — show the user, in plain words, where they stand with MTD.

Two routes:
  * GET /status       full page: a calm progress-toward-the-line view, the
                      headline banner, and any upcoming filing deadlines.
  * GET /status/line  a single .statusline fragment, lazy-loaded into the
                      capture home so that screen paints instantly.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import tax
from ..db import Repo, get_session
from ..models import AppUser
from ..security import require_user
from ..templating import partial, render

router = APIRouter()


@router.get("/status")
def status_page(request: Request, user: AppUser = Depends(require_user),
                s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    st = tax.compute_status(repo, date.today())

    # Upcoming deadlines (only meaningful once mandation is confirmed). We show
    # the whole list for the relevant year so the user sees the rhythm ahead.
    obligations: list[dict] = []
    if st.mandated_year_label:
        start_year = int(st.mandated_year_label.split("/")[0])
        today = date.today()
        for ob in tax.obligations_for_year(date(start_year, 4, 6)):
            days = (ob["due_date"] - today).days
            obligations.append({
                "label": ob["label"],
                "kind": ob["kind"],
                "due_date": ob["due_date"],
                "days_until": days,
                "is_past": days < 0,
            })

    return render(request, "status.html", user=user,
                  active_tab="more", show_tabs=True,
                  st=st, obligations=obligations)


@router.get("/status/line")
def status_line(request: Request, user: AppUser = Depends(require_user),
                s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    st = tax.compute_status(repo, date.today())
    return partial(request, "_status_line.html", user=user, st=st)
