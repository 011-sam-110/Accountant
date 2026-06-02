"""Recurrence detection — read the instructor's schedule back out of his data.

Every logged lesson already carries a date and a pupil, so a regular pupil shows
up as the same student_id recurring on a steady cadence. We surface those as
one-tap "set up a repeat" suggestions, doing the heavy lifting instead of making
him fill in a blank form.

Pure functions over Entry rows (no DB access), so they're cheap to call per
request and easy to unit-test. Pupils only for this first pass (grouped by
student); vendor/expense detection is a planned follow-up.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

# Tunables — deliberately conservative so we never nag on noise.
MIN_OCCURRENCES = 3       # need at least this many lessons to call it a pattern
MIN_SPAN_DAYS = 14        # ...spread over at least this long
RECENCY_DAYS = 35         # ...with the latest this recent (else they've stopped)
VARIES_RATIO = 0.40       # amount spread above this => "varies", confirm on log

# Day-gaps (low, high) that count as each cadence.
_CADENCE_BANDS = [
    ("weekly", 5, 10),
    ("fortnightly", 11, 18),
    ("monthly", 24, 36),
]
_CADENCE_DAYS = {"weekly": 7, "fortnightly": 14, "monthly": 30}
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]


@dataclass
class Suggestion:
    kind: str                    # "student"
    student_id: str
    name: str
    entry_type: str              # "income"
    category_id: str | None
    cadence: str                 # weekly | fortnightly | monthly
    weekday: int | None          # 0=Mon .. 6=Sun
    amount_minor: int            # best-guess (median) amount
    amount_varies: bool          # amounts wobble a lot — worth a glance on log
    count: int                   # how many times we've seen it
    last_date: date
    next_due: date
    confidence: str              # "high" | "medium"

    @property
    def weekday_name(self) -> str:
        return _WEEKDAYS[self.weekday] if self.weekday is not None else ""


def _classify_cadence(gaps: list[int]) -> tuple[str | None, float]:
    """Pick the cadence whose band holds the most gaps; return (cadence, consistency)."""
    if not gaps:
        return None, 0.0
    best, best_hits = None, 0
    for name, lo, hi in _CADENCE_BANDS:
        hits = sum(1 for g in gaps if lo <= g <= hi)
        if hits > best_hits:
            best, best_hits = name, hits
    return best, (best_hits / len(gaps) if best else 0.0)


def _next_due(last: date, cadence: str, weekday: int | None, today: date) -> date:
    step = _CADENCE_DAYS.get(cadence, 7)
    nxt = last + timedelta(days=step)
    while nxt < today:               # predicted date already slipped past
        nxt += timedelta(days=step)
    if weekday is not None and cadence in ("weekly", "fortnightly"):
        nxt += timedelta(days=(weekday - nxt.weekday()) % 7)  # keep usual weekday
    return nxt


def detect_pupil_repeats(
    income_entries,
    *,
    today: date,
    students_by_id: dict,
    exclude_student_ids=frozenset(),
    dismissed_student_ids=frozenset(),
    limit: int = 8,
) -> list[Suggestion]:
    """Find regular pupils in a user's income entries.

    `income_entries` are income Entry rows (not deleted). `students_by_id` maps
    student_id -> object with `.name`; the caller passes *active* pupils only, so
    anyone who's passed their test / cancelled (archived) drops out on their own.
    Pupils already set up as a repeat (`exclude_student_ids`) or dismissed as
    'not a regular' (`dismissed_student_ids`) are skipped.
    """
    groups: dict[str, list] = {}
    for e in income_entries:
        sid = getattr(e, "student_id", None)
        if not sid or sid not in students_by_id:
            continue
        if sid in exclude_student_ids or sid in dismissed_student_ids:
            continue
        groups.setdefault(sid, []).append(e)

    out: list[Suggestion] = []
    for sid, rows in groups.items():
        dates = sorted(e.entry_date for e in rows)
        if len(dates) < MIN_OCCURRENCES:
            continue
        if (dates[-1] - dates[0]).days < MIN_SPAN_DAYS:
            continue
        if (today - dates[-1]).days > RECENCY_DAYS:
            continue  # looks like they've stopped

        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        cadence, consistency = _classify_cadence(gaps)
        if not cadence or consistency < 0.5:
            continue

        amounts = [e.amount_minor for e in rows if e.amount_minor]
        if not amounts:
            continue
        amt = int(median(amounts))
        varies = (max(amounts) - min(amounts)) / amt > VARIES_RATIO

        weekday = Counter(d.weekday() for d in dates).most_common(1)[0][0]
        cats = Counter(e.category_id for e in rows if e.category_id)
        category_id = cats.most_common(1)[0][0] if cats else None
        confidence = "high" if (consistency >= 0.8 and len(dates) >= 5) else "medium"

        out.append(Suggestion(
            kind="student", student_id=sid, name=students_by_id[sid].name,
            entry_type="income", category_id=category_id, cadence=cadence,
            weekday=weekday, amount_minor=amt, amount_varies=varies,
            count=len(dates), last_date=dates[-1],
            next_due=_next_due(dates[-1], cadence, weekday, today),
            confidence=confidence,
        ))

    out.sort(key=lambda s: (-s.count, s.next_due))   # most evidence first
    return out[:limit]
