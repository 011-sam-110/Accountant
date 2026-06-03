"""The CATEGORIES slice — rename / favourite / hide / merge / delete the
*visible* labels. The hidden SA103 box mapping is never touched here, so
renaming is purely cosmetic and exports stay correct.

Most mutations swap a single `_category_row.html`. Creating, merging and a
failed delete re-render the whole `categories.html` into `#screen` because
they change the shape of a whole group (add/move/remove rows), which is far
simpler and more robust than juggling out-of-band fragments.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import Repo, get_session
from ..models import AppUser
from ..sa103 import SA103_BOXES
from ..security import require_user
from ..templating import partial, render

router = APIRouter()


def _page_ctx(repo: Repo, *, toast: str | None = None,
              warn: str | None = None) -> dict:
    return {
        "income_categories": repo.categories(kind="income", include_hidden=True),
        "expense_categories": repo.categories(kind="expense", include_hidden=True),
        "income_boxes": [b for b in SA103_BOXES if b["kind"] == "income"],
        "expense_boxes": [b for b in SA103_BOXES if b["kind"] == "expense"],
        "toast": toast,
        "warn": warn,
    }


@router.get("/categories")
def categories(request: Request, user: AppUser = Depends(require_user),
               s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    return render(request, "categories.html", user=user,
                  active_tab="more", show_tabs=True, **_page_ctx(repo))


@router.post("/categories")
def category_create(request: Request, label: str = Form(""),
                    sa103_code: str = Form(""), kind: str = Form(""),
                    is_favourite: str = Form(""),
                    user: AppUser = Depends(require_user),
                    s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    label = label.strip()
    if not label or kind not in ("income", "expense") or not sa103_code:
        return render(request, "categories.html", user=user,
                      active_tab="more", show_tabs=True,
                      **_page_ctx(repo, warn="Please give it a name and pick a tax box."))
    repo.create_category(label, sa103_code, kind, is_favourite=bool(is_favourite))
    return render(request, "categories.html", user=user,
                  active_tab="more", show_tabs=True,
                  **_page_ctx(repo, toast=f"Added “{label}”."))


# NOTE: this literal route MUST be declared before "/categories/{category_id}",
# otherwise FastAPI matches "merge" as a category_id and the update handler runs.
@router.post("/categories/merge")
def category_merge(request: Request, src_id: str = Form(""),
                   dst_id: str = Form(""),
                   user: AppUser = Depends(require_user),
                   s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    ok, msg = repo.merge_categories(src_id, dst_id)
    ctx = _page_ctx(repo, toast=msg) if ok else _page_ctx(repo, warn=msg)
    return render(request, "categories.html", user=user,
                  active_tab="more", show_tabs=True, **ctx)


@router.post("/categories/{category_id}")
def category_update(category_id: str, request: Request,
                    label: str | None = Form(None),
                    is_favourite: str | None = Form(None),
                    is_hidden: str | None = Form(None),
                    display_order: str | None = Form(None),
                    user: AppUser = Depends(require_user),
                    s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    fields: dict = {}
    # The star / hide toggles send the desired next state explicitly: "1" = on,
    # "0" = off. (We can't use an empty string for off: FastAPI's Form() treats
    # an empty value as absent, so the toggle could never be turned back off.)
    # A field that is None simply wasn't sent (e.g. rename) and is left alone.
    if label is not None and label.strip():
        fields["label"] = label.strip()
    if is_favourite is not None:
        fields["is_favourite"] = is_favourite == "1"
    if is_hidden is not None:
        fields["is_hidden"] = is_hidden == "1"
    if display_order is not None and str(display_order).strip():
        try:
            fields["display_order"] = int(display_order)
        except ValueError:
            pass
    c = repo.update_category(category_id, **fields)
    if c is None:
        return Response("", status_code=200)
    return partial(request, "_category_row.html", user=user, category=c)


@router.post("/categories/{category_id}/delete")
def category_delete(category_id: str, request: Request,
                    user: AppUser = Depends(require_user),
                    s: Session = Depends(get_session)):
    repo = Repo(s, user.id)
    c = repo.get_category(category_id)
    ok, msg = repo.delete_category(category_id)
    if ok:
        # Remove the row + show a confirmation toast (out-of-band).
        return partial(request, "_category_row.html", user=user, category=None,
                       removed=True, toast=msg)
    # Blocked (has entries): hand the row back unchanged with the reason.
    return partial(request, "_category_row.html", user=user, category=c, warn=msg)
