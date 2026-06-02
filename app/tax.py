"""The MTD compliance brain — mandation engine, date math, plain-English copy.

Everything here is a PURE function over a `Repo` + a `today` date, so it is
trivially testable and the routes / cron stay thin. Money is integer pennies.

The hard part of MTD is *mandation* — deciding whether HMRC requires this
person to file quarterly yet. The rules (verified to June 2026):

  * The UK tax year runs 6 Apr → 5 Apr.
  * Mandation is tested on a PAST qualifying-income return, not the year you're
    in. A given completed tax year's turnover decides mandation TWO tax years
    later:  2024/25 → 2026/27 (£50k line) · 2025/26 → 2027/28 (£30k) ·
    2026/27 → 2028/29 (£20k).  (So mandation-year-start = prior-year-start + 2.)
  * The threshold is keyed to the MANDATION year it gates (see config).
  * Mandation is only CONFIRMED once that prior-year figure actually exists.
    The year you're *in* can only ever be an EARLY WARNING — we must never tell
    a non-technical user "you must file quarterly" on an unfinished year.
  * Once mandated, you stay mandated (exemptions are out of scope here).

Qualifying income = GROSS turnover before expenses → repo.gross_income_minor.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from .config import (
    DEFAULT_THRESHOLD_MINOR,
    FINAL_DECLARATION,
    MTD_THRESHOLDS_MINOR,
    QUARTER_DEADLINES,
)
from .db import Repo

# ---------------------------------------------------------------- states ----
# A small set of states the whole UI keys off. Ordered loosely calm → louder.
UNDER_THRESHOLD = "under_threshold"          # comfortably below the line
APPROACHING = "approaching"                   # in-progress YTD > 80% of the line
ON_TRACK_TO_BE_MANDATED = "on_track"          # in-progress projection over the line (NOT confirmed)
MANDATED_NEXT_YEAR = "mandated_next_year"     # a confirmed prior figure → quarterly starts next 6 Apr
MANDATED_ACTIVE = "mandated_active"           # currently inside a mandated tax year

# Fraction of the line at which we start gently flagging "approaching".
APPROACHING_FRACTION = 0.80

# Year offset from a completed tax year's START to the mandation year it governs.
MANDATION_LAG_YEARS = 2


def threshold_for_mandation_year(start_year: int) -> int:
    """Pennies threshold for the mandation year beginning 6 Apr `start_year`.

    Falls back to the highest known line (£50k) for years before the table.
    """
    return MTD_THRESHOLDS_MINOR.get(start_year, DEFAULT_THRESHOLD_MINOR)


# ----------------------------------------------------------- obligations ----
def obligations_for_year(tax_year_start: date) -> list[dict]:
    """All filing obligations for the tax year beginning on `tax_year_start`.

    Built purely from config so HMRC date changes stay out of code. Returns a
    chronologically ordered list of {label, due_date, kind} where kind is
    'quarterly' (the four updates) or 'final' (the Final Declaration).
    """
    sy = tax_year_start.year
    out: list[dict] = []
    for label, (month, day), year_offset in QUARTER_DEADLINES:
        out.append({
            "label": label,
            "due_date": date(sy + year_offset, month, day),
            "kind": "quarterly",
        })
    (fmonth, fday), foffset = FINAL_DECLARATION
    out.append({
        "label": "Final",
        "due_date": date(sy + foffset, fmonth, fday),
        "kind": "final",
    })
    out.sort(key=lambda o: o["due_date"])
    return out


def next_obligation(today: date, tax_year_start: date) -> tuple[dict | None, int | None]:
    """Soonest obligation whose due_date is today or later for that year.

    Returns (obligation, days_until). (None, None) once the year's obligations
    are all in the past.
    """
    upcoming = [o for o in obligations_for_year(tax_year_start) if o["due_date"] >= today]
    if not upcoming:
        return None, None
    nxt = min(upcoming, key=lambda o: o["due_date"])
    return nxt, (nxt["due_date"] - today).days


# --------------------------------------------------------------- status ----
@dataclass
class Status:
    state: str
    income_minor: int                 # in-progress (current) tax-year gross so far
    threshold_minor: int              # the line that's relevant right now
    pct: float                        # income / threshold (0..1+; cap on display)
    prior_year_label: str | None
    prior_income_minor: int | None
    next_obligation: dict | None
    days_until: int | None
    tone: str                         # 'ok' | 'warn'
    headline: str                     # one reassuring sentence
    detail: str                       # 1–2 plain sentences
    current_year_label: str | None = None
    mandated_year_label: str | None = None  # which tax year quarterly filing applies to

    @property
    def pct_display(self) -> int:
        """Whole-percent for a progress bar width, capped at 100."""
        return max(0, min(100, round(self.pct * 100)))


def _pounds_word(minor: int) -> str:
    """'£50,000' style (no pence) for friendly copy."""
    return f"£{minor // 100:,}"


def nice_date(d: date) -> str:
    """'2 Jun 2026' — portable (strftime '%-d' isn't on Windows)."""
    if isinstance(d, date):
        return f"{d.day} {calendar.month_abbr[d.month]} {d.year}"
    return str(d or "")


def _latest_completed_prior_year(repo: Repo, today: date):
    """Most recent tax year that has genuinely finished (end_date < today).

    repo.tax_years() is newest-first, so the first match is the latest.
    """
    for ty in repo.tax_years():
        if ty.end_date < today:
            return ty
    return None


def compute_status(repo: Repo, today: date) -> Status:
    """The whole compliance picture for one user as of `today`.

    Reasoning, step by step:
      1. The current tax year is the one containing `today`.
      2. Look for the most recent COMPLETED prior year. Its turnover is tested
         against the line for the mandation year it governs (start + 2). If it
         clears the line, mandation is CONFIRMED — either already active (the
         mandation year has begun) or starting next 6 Apr.
      3. If nothing is confirmed, fall back to the in-progress year-to-date
         figure for an EARLY WARNING only: >80% of the line ⇒ "approaching",
         otherwise "under threshold". We never escalate an unfinished year to a
         "you must file" message.
    """
    current = repo.current_tax_year()
    current_income = repo.gross_income_minor(current.id)
    current_start_year = current.start_date.year

    prior = _latest_completed_prior_year(repo, today)
    prior_label = prior.label if prior else None
    prior_income = repo.gross_income_minor(prior.id) if prior else None

    # ---- 1) Is mandation CONFIRMED by a completed prior year? ----
    if prior is not None and prior_income is not None:
        mandation_start_year = prior.start_date.year + MANDATION_LAG_YEARS
        line = threshold_for_mandation_year(mandation_start_year)
        if prior_income >= line:
            mandated_start = date(mandation_start_year, 4, 6)
            mandated_label = f"{mandation_start_year}/{str(mandation_start_year + 1)[2:]}"
            # Active if we're already in (or past the start of) the mandation year.
            if today >= mandated_start:
                nxt, days = next_obligation(today, mandated_start)
                state = MANDATED_ACTIVE
            else:
                # Confirmed but not yet begun → reminders track the coming year.
                nxt, days = next_obligation(today, mandated_start)
                state = MANDATED_NEXT_YEAR
            headline, detail, tone = status_copy(
                state, threshold_minor=line, next_obligation=nxt, days_until=days,
                mandated_label=mandated_label)
            return Status(
                state=state,
                income_minor=current_income,
                threshold_minor=line,
                pct=(prior_income / line) if line else 0.0,
                prior_year_label=prior_label,
                prior_income_minor=prior_income,
                next_obligation=nxt,
                days_until=days,
                tone=tone,
                headline=headline,
                detail=detail,
                current_year_label=current.label,
                mandated_year_label=mandated_label,
            )

    # ---- 2) Not confirmed → EARLY WARNING off the in-progress year ----
    # The in-progress year's turnover would itself be tested two years out, so
    # we compare it to that line for the warning band.
    warn_mandation_year = current_start_year + MANDATION_LAG_YEARS
    line = threshold_for_mandation_year(warn_mandation_year)
    pct = (current_income / line) if line else 0.0
    state = APPROACHING if current_income >= line * APPROACHING_FRACTION else UNDER_THRESHOLD
    headline, detail, tone = status_copy(state, threshold_minor=line)
    return Status(
        state=state,
        income_minor=current_income,
        threshold_minor=line,
        pct=pct,
        prior_year_label=prior_label,
        prior_income_minor=prior_income,
        next_obligation=None,
        days_until=None,
        tone=tone,
        headline=headline,
        detail=detail,
        current_year_label=current.label,
        mandated_year_label=None,
    )


def status_copy(state: str, *, threshold_minor: int,
                next_obligation: dict | None = None, days_until: int | None = None,
                mandated_label: str | None = None) -> tuple[str, str, str]:
    """Plain-English (headline, detail, tone) for a state. No jargon, reassuring."""
    line = _pounds_word(threshold_minor)

    if state == UNDER_THRESHOLD:
        return (
            f"You're under the {line} line, so a simple yearly summary is all "
            "HMRC needs for now.",
            "Keep snapping receipts and logging payments — we'll keep watching "
            "the line for you and let you know well in advance if anything changes.",
            "ok",
        )

    if state == APPROACHING:
        return (
            "You're getting close to the line where the rules change — nothing "
            "to do yet, we're keeping an eye on it.",
            f"If your income for the year stays above {line}, HMRC may ask you to "
            "send updates every 3 months in a future year. We'll tell you in "
            "plenty of time — there's nothing to change today.",
            "warn",
        )

    if state == ON_TRACK_TO_BE_MANDATED:
        return (
            "You're on track to go over the line this year — still nothing to do "
            "right now, and we'll guide you when it counts.",
            f"At this pace your turnover looks likely to pass {line}. That only "
            "matters for a future year, and we'll handle the reminders. Today, "
            "carry on as normal.",
            "warn",
        )

    if state == MANDATED_NEXT_YEAR:
        when = ""
        if next_obligation is not None and days_until is not None:
            when = f" Your first update will be due {nice_date(next_obligation['due_date'])}."
        return (
            "Heads up: from 6 April you'll need to send HMRC an update every 3 "
            "months instead of one yearly return. We'll handle the reminders — "
            "nothing changes today.",
            ("Because a past year went over the line, HMRC will ask for quarterly "
             "updates from the next tax year. There's nothing to do right now — "
             "just keep logging as usual and we'll prompt you when each one is due."
             + when),
            "warn",
        )

    if state == MANDATED_ACTIVE:
        if next_obligation is not None and days_until is not None:
            due_str = nice_date(next_obligation["due_date"])
            tone = "warn" if days_until <= 14 else "ok"
            day_word = "day" if days_until == 1 else "days"
            return (
                f"You're sending HMRC updates every 3 months now. Your next one "
                f"is due {due_str} ({days_until} {day_word}) — we'll remind you.",
                "There's nothing to file by hand — when each update is due we'll "
                "walk you through it and send you a reminder first.",
                tone,
            )
        return (
            "You're sending HMRC updates every 3 months now — and you're all "
            "caught up. We'll remind you before the next one.",
            "Nothing's due at the moment. Keep logging as you go and we'll prompt "
            "you in good time when the next update comes around.",
            "ok",
        )

    # Defensive fallback — should be unreachable.
    return (
        f"You're under the {line} line, so a simple yearly summary is all HMRC "
        "needs for now.",
        "We'll keep an eye on things and let you know if anything changes.",
        "ok",
    )
