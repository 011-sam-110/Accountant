"""Reminder engine — work out which nudges are due today and send them once.

Design (plan §7, "at-least-once" idempotency):
  * `compute_due` is pure: given a user + today it returns the list of reminder
    items that fall on a firing window *today*. No DB writes, no sends.
  * `send_due` is the side-effecting half: it dedupes against the ReminderLog,
    sends via notify, and ONLY logs the send if notify confirmed success — so a
    transient email failure is retried tomorrow instead of being silently lost.
  * `run_all` opens its own session (it's called from the cron, outside a
    request) and fans out across every user.

Firing windows (days from today to the due date, T):
  quarterly: T-21, T-7, T-1, T-0, then overdue +1, +3, +7
  final:     T-30, T-14, T-7, T-1, T-0
Channels: email for everything; SMS *also* for the high-stakes ones (T-1, T-0,
anything overdue, and every final-declaration nudge) — but only if the user has
opted into SMS and given a phone number.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from . import emails, tax
from .db import Repo, SessionLocal
from .models import AppUser
from .notify import send_email, send_sms

# Offsets in DAYS-UNTIL-DUE that trigger a reminder. Negative = overdue.
QUARTERLY_WINDOWS = [21, 7, 1, 0, -1, -3, -7]
FINAL_WINDOWS = [30, 14, 7, 1, 0]

# High-stakes windows that also get an SMS (if opted in). 0/1 and any overdue.
HIGH_STAKES_DAYS = {1, 0, -1, -3, -7}


def _milestone(days_until: int) -> str:
    """'T-7' for upcoming, 'OVERDUE+3' once past the due date."""
    return f"OVERDUE+{abs(days_until)}" if days_until < 0 else f"T-{days_until}"


def _active_mandated_start(st: "tax.Status") -> date | None:
    """The 6-Apr start of the tax year whose obligations we should remind for.

    Only MANDATED_ACTIVE / MANDATED_NEXT_YEAR have real obligations. We recover
    the year from the status' mandated_year_label (e.g. '2027/28' → 2027-04-06).
    """
    if st.state not in (tax.MANDATED_ACTIVE, tax.MANDATED_NEXT_YEAR):
        return None
    if not st.mandated_year_label:
        return None
    start_year = int(st.mandated_year_label.split("/")[0])
    return date(start_year, 4, 6)


def _subject_body(obligation: dict, days_until: int, name: str) -> tuple[str, str]:
    """Reassuring, plain-English copy for a single reminder."""
    who = name.split()[0] if name else "there"
    due_str = tax.nice_date(obligation["due_date"])
    is_final = obligation["kind"] == "final"
    thing = ("your final yearly summary for HMRC" if is_final
             else "your 3-monthly update for HMRC")

    if days_until > 1:
        subject = f"Coming up: {thing} is due {due_str}"
        body = (
            f"Hi {who},\n\n"
            f"Just a friendly heads-up — {thing} is due on {due_str} "
            f"({days_until} days away). There's nothing you need to do this "
            f"minute; we'll walk you through it when you're ready.\n\n"
            f"You can review your figures any time in Tidy Books.\n"
        )
    elif days_until == 1:
        subject = f"Tomorrow: {thing} is due {due_str}"
        body = (
            f"Hi {who},\n\n"
            f"A quick reminder that {thing} is due tomorrow, {due_str}. "
            f"It only takes a few minutes — open Tidy Books and we'll guide you "
            f"through it step by step.\n"
        )
    elif days_until == 0:
        subject = f"Today: {thing} is due"
        body = (
            f"Hi {who},\n\n"
            f"Today's the day — {thing} is due. Open Tidy Books whenever you "
            f"have a few minutes and we'll help you get it sent. Nothing to "
            f"worry about; we've kept everything ready for you.\n"
        )
    else:
        subject = f"Gentle nudge: {thing} was due {due_str}"
        body = (
            f"Hi {who},\n\n"
            f"It looks like {thing} (due {due_str}) hasn't gone in yet. No "
            f"panic — it's quick to sort. Open Tidy Books and we'll help you "
            f"catch up right away.\n"
        )
    return subject, body


def _sms_body(obligation: dict, days_until: int) -> str:
    due_str = tax.nice_date(obligation["due_date"])
    thing = ("final yearly summary" if obligation["kind"] == "final"
             else "3-monthly HMRC update")
    if days_until < 0:
        return (f"Tidy Books: your {thing} (due {due_str}) hasn't gone in yet — "
                f"open the app when you can and we'll help you catch up.")
    if days_until == 0:
        return (f"Tidy Books: your {thing} is due today. Open the app and we'll "
                f"walk you through it.")
    return (f"Tidy Books: your {thing} is due tomorrow ({due_str}). Open the app "
            f"and we'll help you send it.")


def compute_due(repo: Repo, user: AppUser, today: date) -> list[dict]:
    """Reminder items that fire for `user` today. Pure — no sends, no writes.

    Each item: {obligation, milestone, channel, subject, body}. The same
    obligation/milestone may appear twice (once email, once sms) when SMS is
    warranted and enabled.
    """
    st = tax.compute_status(repo, today)
    mandated_start = _active_mandated_start(st)
    if mandated_start is None:
        return []  # not mandated → no obligations to remind about

    items: list[dict] = []
    for ob in tax.obligations_for_year(mandated_start):
        days_until = (ob["due_date"] - today).days
        windows = FINAL_WINDOWS if ob["kind"] == "final" else QUARTERLY_WINDOWS
        if days_until not in windows:
            continue

        milestone = _milestone(days_until)
        subject, body = _subject_body(ob, days_until, user.display_name or "")

        # Email always (if opted in — the user can mute email).
        if user.email_opt_in and user.email:
            items.append({
                "obligation": ob["label"],
                "milestone": milestone,
                "channel": "email",
                "subject": subject,
                "body": body,
            })

        # SMS only for high-stakes windows + every final-declaration nudge,
        # and only when the user has opted in and given a number.
        high_stakes = (days_until in HIGH_STAKES_DAYS) or (ob["kind"] == "final")
        if high_stakes and user.sms_opt_in and user.phone:
            items.append({
                "obligation": ob["label"],
                "milestone": milestone,
                "channel": "sms",
                "subject": subject,            # unused for sms, kept for symmetry
                "body": _sms_body(ob, days_until),
            })

    return items


def send_due(s, user: AppUser, today: date) -> dict:
    """Send today's due reminders for one user, idempotently.

    Dedupe key = "{user.id}:{obligation}:{milestone}". We log the send ONLY
    after notify confirms success, so a failed send is naturally retried on the
    next run (at-least-once). The caller's session auto-commits.
    """
    repo = Repo(s, user.id)
    sent: list[dict] = []
    skipped = 0

    for item in compute_due(repo, user, today):
        dedupe_key = f"{user.id}:{item['obligation']}:{item['milestone']}"
        if repo.reminder_sent(dedupe_key):
            skipped += 1
            continue

        if item["channel"] == "sms":
            ok = send_sms(user.phone, item["body"])
        else:
            ok = send_email(user.email, item["subject"],
                            emails.reminder_email(item["body"]),
                            text=item["body"])

        if ok and repo.log_reminder(dedupe_key, item["obligation"],
                                    item["milestone"], item["channel"]):
            sent.append({
                "user_id": user.id,
                "obligation": item["obligation"],
                "milestone": item["milestone"],
                "channel": item["channel"],
            })
        else:
            # Either the send failed (retry tomorrow) or a concurrent run beat
            # us to the log (already counted). Neither is an error.
            skipped += 1

    return {"sent": sent, "skipped": skipped}


def run_all(today: date | None = None) -> dict:
    """Cron entrypoint: open a fresh session, fan out across every user.

    Returns a combined summary {sent: [...], skipped: n, users: n}.
    """
    today = today or date.today()
    all_sent: list[dict] = []
    skipped = 0
    n_users = 0
    with SessionLocal() as s:
        for user in s.scalars(select(AppUser)).all():
            n_users += 1
            res = send_due(s, user, today)
            all_sent.extend(res["sent"])
            skipped += res["skipped"]
        s.commit()
    return {"sent": all_sent, "skipped": skipped, "users": n_users}
