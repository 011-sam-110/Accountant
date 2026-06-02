"""Small shared helpers: ids, time, and forgiving money/date parsing.

Money is stored as BIGINT pennies everywhere (never floats — IEEE-754 drift
can't reconcile a tax return). Convert pounds<->pennies only at the UI edge.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Naive UTC — consistent with sqlite (which drops tzinfo)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- money ----
def to_minor(pounds: float | str | int) -> int:
    """Pounds (any reasonable form) -> integer pennies. Raises ValueError."""
    return parse_amount_to_minor(pounds)


def from_minor(minor: int) -> float:
    return round((minor or 0) / 100, 2)


def format_pounds(minor: int | None, *, signed: bool = False, kind: str | None = None) -> str:
    """Render pennies as '£1,234.50'. If signed, prefix +/- (never colour-only)."""
    minor = minor or 0
    sign = ""
    if signed:
        # Money in is always +, money out always − (never colour-only). When the
        # kind isn't known (e.g. a net figure), fall back to the value's sign.
        if kind == "income":
            sign = "+"
        elif kind == "expense":
            sign = "−"
        else:
            sign = "−" if minor < 0 else "+"
    return f"{sign}£{abs(minor) / 100:,.2f}"


_AMOUNT_RE = re.compile(r"[^0-9.,-]")


def parse_amount_to_minor(raw) -> int:
    """Forgiving amount parser. Accepts '£12.50', '12,50', '1,234.56', 61.4, etc.

    Returns integer pennies. Raises ValueError on anything unparseable.
    """
    if raw is None:
        raise ValueError("empty amount")
    if isinstance(raw, (int,)) and not isinstance(raw, bool):
        return int(raw * 100)
    if isinstance(raw, float):
        return int(round(raw * 100))
    s = str(raw).strip()
    if not s:
        raise ValueError("empty amount")
    s = _AMOUNT_RE.sub("", s)
    if not s or s in {"-", ".", ","}:
        raise ValueError(f"unparseable amount: {raw!r}")
    neg = s.startswith("-")
    s = s.lstrip("-")
    # Decide decimal separator. If both present, the rightmost is the decimal.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # "12,50" -> decimal comma; "1,234" -> thousands
        whole, _, frac = s.rpartition(",")
        s = f"{whole}.{frac}" if len(frac) == 2 else s.replace(",", "")
    val = round(float(s) * 100)
    return -int(val) if neg else int(val)


_DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y",
    "%d.%m.%Y", "%d.%m.%y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
]


def parse_uk_date(raw, *, default: date | None = None) -> date:
    """Parse common UK date formats (day-first). Falls back to `default`/today."""
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if raw:
        s = str(raw).strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    if default is not None:
        return default
    return date.today()


# ------------------------------------------------------------ tax year ----
def tax_year_bounds(d: date) -> tuple[date, date, str]:
    """UK tax year (6 Apr -> 5 Apr) containing date `d`.

    Returns (start_date, end_date, label) e.g. (2026-04-06, 2027-04-05, '2026/27').
    """
    start_year = d.year if (d.month, d.day) >= (4, 6) else d.year - 1
    start = date(start_year, 4, 6)
    end = date(start_year + 1, 4, 5)
    label = f"{start_year}/{str(start_year + 1)[2:]}"
    return start, end, label


def current_tax_year_bounds(today: date | None = None) -> tuple[date, date, str]:
    return tax_year_bounds(today or date.today())
