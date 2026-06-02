"""The CRON slice — a scheduler hits this daily to fire due reminders.

CSRF-exempt by path (api/index.py exempts everything under /api/cron). In
PRODUCTION this MUST be gated by a shared secret (e.g. a `X-Cron-Secret`
header or Vercel Cron's signed request) so the public internet can't trigger
sends — see the TODO in the POST handler. Left open here so it's trivially
inspectable during local review.

  * GET  /api/cron/reminders  → DRY RUN: returns exactly what WOULD be sent
                                today, sending nothing. Safe to poke by hand.
  * POST /api/cron/reminders  → the real run: send + log (idempotent).
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from sqlalchemy import select

from .. import reminders
from ..db import Repo, SessionLocal
from ..models import AppUser

router = APIRouter()


@router.post("/api/cron/reminders")
def run_reminders():
    """Fire all due reminders for every user (idempotent). Returns a summary.

    TODO (prod): require a CRON_SECRET — compare a header against an env var and
    return 401 if it doesn't match, so only the scheduler can invoke this.
    """
    summary = reminders.run_all()
    return summary


@router.get("/api/cron/reminders")
def preview_reminders():
    """DRY RUN — what would go out today, without sending or logging anything."""
    today = date.today()
    would_send: list[dict] = []
    n_users = 0
    with SessionLocal() as s:
        for user in s.scalars(select(AppUser)).all():
            n_users += 1
            repo = Repo(s, user.id)
            for item in reminders.compute_due(repo, user, today):
                dedupe_key = f"{user.id}:{item['obligation']}:{item['milestone']}"
                would_send.append({
                    "user_id": user.id,
                    "email": user.email,
                    "obligation": item["obligation"],
                    "milestone": item["milestone"],
                    "channel": item["channel"],
                    "subject": item["subject"],
                    "already_sent": repo.reminder_sent(dedupe_key),
                })
    return {"dry_run": True, "date": today.isoformat(),
            "users": n_users, "would_send": would_send}
