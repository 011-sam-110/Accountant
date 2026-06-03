"""Engine, session, scoped repository, and seeding.

The Repo is the ONLY place tenant data is read/written. It is bound to a
`user_id` and injects it into every query (`WHERE ... AND user_id = :uid`),
so no route can accidentally read across tenants (plan §5/§8 anti-IDOR).
Cross-user ids simply return None (the route turns that into 404, not 403).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import (
    AppUser, AuditEvent, Base, Category, Entry, OtpCode, Receipt, Repeat,
    RepeatDismissal, ReminderLog, Sa103Box, Session as SessionRow, Student,
    TaxYear,
)
from .sa103 import DEFAULT_CATEGORIES, SA103_BOXES, SA103_BY_CODE
from .util import new_id, tax_year_bounds, utcnow

# --------------------------------------------------------------- engine ----
import json
from datetime import date as _date


def _json_default(o):
    # JSON columns (audit diff, tax-year snapshot) may hold dates/Decimals.
    if isinstance(o, _date):
        return o.isoformat()
    try:
        return float(o)  # Decimal et al.
    except (TypeError, ValueError):
        return str(o)


def _json_dumps(obj) -> str:
    return json.dumps(obj, default=_json_default)


def _normalise_pg_url(url: str) -> str:
    """Force the psycopg3 dialect for any Postgres URL.

    We ship psycopg v3 (`psycopg[binary]`), not psycopg2. SQLAlchemy maps the
    bare `postgres://` / `postgresql://` schemes (and `+psycopg2`) to the
    psycopg2 driver, which isn't installed, so it crashes on import in prod.
    Rewrite the scheme to `postgresql+psycopg://` so the installed driver is
    used regardless of how DATABASE_URL is written.
    """
    for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


_url = _normalise_pg_url(settings.database_url)
_kwargs: dict = {"pool_pre_ping": True, "future": True, "json_serializer": _json_dumps}
if _url.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
else:
    # Neon pooled endpoint (PgBouncer transaction mode): tiny pool, and disable
    # server-side prepared statements (psycopg3) which break under txn pooling.
    _kwargs.update(pool_size=2, max_overflow=0,
                   connect_args={"prepare_threshold": None})

engine = create_engine(_url, **_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_session():
    """FastAPI dependency: one session per request, commit on success."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# --------------------------------------------------------------- seeding ----
def init_db() -> None:
    """Create tables (dev/sqlite) and seed the global SA103 layer.

    In production tables come from Alembic; create_all is a no-op safety net.
    """
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed_sa103(s)
        s.commit()


def seed_sa103(s: Session) -> None:
    existing = {c for (c,) in s.execute(select(Sa103Box.code)).all()}
    for b in SA103_BOXES:
        if b["code"] not in existing:
            s.add(Sa103Box(code=b["code"], box_number=b["box_number"],
                           kind=b["kind"], label=b["label"]))


# ---- user lifecycle (module-level: runs before a user_id is scoped) ----
def get_user_by_email(s: Session, email: str) -> AppUser | None:
    return s.scalar(select(AppUser).where(AppUser.email == email.strip().lower()))


def get_user_by_id(s: Session, uid: str) -> AppUser | None:
    return s.get(AppUser, uid)


def create_user(s: Session, email: str, display_name: str = "") -> AppUser:
    """Create a user and seed their default categories + current tax year."""
    user = AppUser(email=email.strip().lower(), display_name=display_name or "")
    s.add(user)
    s.flush()
    seed_user_categories(s, user.id)
    Repo(s, user.id).get_or_create_tax_year(date.today())
    s.flush()
    return user


def seed_user_categories(s: Session, user_id: str) -> None:
    for order, (label, code, kind, fav) in enumerate(DEFAULT_CATEGORIES):
        s.add(Category(user_id=user_id, sa103_code=code, label=label, kind=kind,
                       display_order=order, is_favourite=fav))


# ----- session-row helpers (revocation) -----
def create_session_row(s: Session, user_id: str, expires_at: datetime,
                       user_agent: str | None = None) -> SessionRow:
    row = SessionRow(user_id=user_id, expires_at=expires_at, user_agent=user_agent)
    s.add(row)
    s.flush()
    return row


def get_session_row(s: Session, sid: str) -> SessionRow | None:
    return s.get(SessionRow, sid)


# =====================================================================
#  Scoped repository
# =====================================================================
class Repo:
    def __init__(self, session: Session, user_id: str):
        self.s = session
        self.uid = user_id

    # ---- audit ----
    def audit(self, entity: str, entity_id: str, action: str, diff: dict | None = None):
        self.s.add(AuditEvent(user_id=self.uid, entity=entity, entity_id=entity_id,
                              action=action, diff=diff))

    # ---------------------------------------------------- categories ----
    def categories(self, *, kind: str | None = None, include_hidden: bool = True,
                   include_deleted: bool = False) -> list[Category]:
        q = select(Category).where(Category.user_id == self.uid)
        if kind:
            q = q.where(Category.kind == kind)
        if not include_hidden:
            q = q.where(Category.is_hidden.is_(False))
        if not include_deleted:
            q = q.where(Category.is_deleted.is_(False))
        q = q.order_by(Category.display_order, Category.label)
        return list(self.s.scalars(q).all())

    def favourite_categories(self, kind: str | None = None) -> list[Category]:
        return [c for c in self.categories(kind=kind, include_hidden=False)
                if c.is_favourite]

    def get_category(self, cid: str) -> Category | None:
        return self.s.scalar(select(Category).where(
            Category.id == cid, Category.user_id == self.uid))

    def create_category(self, label: str, sa103_code: str, kind: str,
                        is_favourite: bool = False) -> Category:
        order = (self.s.scalar(select(func.coalesce(func.max(Category.display_order), 0))
                               .where(Category.user_id == self.uid)) or 0) + 1
        c = Category(user_id=self.uid, sa103_code=sa103_code, label=label, kind=kind,
                     display_order=order, is_favourite=is_favourite)
        self.s.add(c)
        self.s.flush()
        self.audit("category", c.id, "create", {"label": label})
        return c

    def update_category(self, cid: str, **fields) -> Category | None:
        c = self.get_category(cid)
        if not c:
            return None
        allowed = {"label", "display_order", "is_favourite", "is_hidden"}
        for k, v in fields.items():
            if k in allowed:
                setattr(c, k, v)
        self.s.flush()
        self.audit("category", cid, "update", {k: v for k, v in fields.items()})
        return c

    def merge_categories(self, src_id: str, dst_id: str) -> tuple[bool, str]:
        src, dst = self.get_category(src_id), self.get_category(dst_id)
        if not src or not dst or src_id == dst_id:
            return False, "Pick two different categories."
        if SA103_BY_CODE.get(src.sa103_code, {}).get("box_number") != \
           SA103_BY_CODE.get(dst.sa103_code, {}).get("box_number"):
            return False, "Those map to different tax boxes, so they can't be merged."
        self.s.execute(
            Entry.__table__.update()
            .where(Entry.user_id == self.uid, Entry.category_id == src_id)
            .values(category_id=dst_id))
        src.is_deleted = True
        self.s.flush()
        self.audit("category", src_id, "merge", {"into": dst_id})
        return True, f"Merged into {dst.label}."

    def delete_category(self, cid: str) -> tuple[bool, str]:
        c = self.get_category(cid)
        if not c:
            return False, "Category not found."
        n = self.s.scalar(select(func.count()).select_from(Entry).where(
            Entry.user_id == self.uid, Entry.category_id == cid,
            Entry.is_deleted.is_(False)))
        if n:
            return False, f"That category still has {n} entr{'y' if n == 1 else 'ies'}. Move them first."
        c.is_deleted = True
        self.s.flush()
        self.audit("category", cid, "delete", None)
        return True, "Category removed."

    def most_used_category(self, kind: str) -> Category | None:
        row = self.s.execute(
            select(Entry.category_id, func.count().label("n"))
            .join(Category, Category.id == Entry.category_id)
            .where(Entry.user_id == self.uid, Category.kind == kind,
                   Entry.is_deleted.is_(False))
            .group_by(Entry.category_id).order_by(func.count().desc())).first()
        return self.get_category(row[0]) if row and row[0] else None

    def default_category(self, kind: str) -> Category | None:
        favs = self.favourite_categories(kind)
        if favs:
            return favs[0]
        return self.most_used_category(kind) or next(
            iter(self.categories(kind=kind, include_hidden=False)), None)

    # ------------------------------------------------------ students ----
    def students(self, include_archived: bool = False) -> list[Student]:
        q = select(Student).where(Student.user_id == self.uid)
        if not include_archived:
            q = q.where(Student.is_archived.is_(False))
        return list(self.s.scalars(q.order_by(Student.name)).all())

    def get_student(self, sid: str) -> Student | None:
        return self.s.scalar(select(Student).where(
            Student.id == sid, Student.user_id == self.uid))

    def get_or_create_student(self, name: str) -> Student:
        name = (name or "").strip()
        existing = self.s.scalar(select(Student).where(
            Student.user_id == self.uid, func.lower(Student.name) == name.lower()))
        if existing:
            return existing
        st = Student(user_id=self.uid, name=name)
        self.s.add(st)
        self.s.flush()
        return st

    def archive_student(self, sid: str):
        st = self.get_student(sid)
        if st:
            st.is_archived = True
            self.s.flush()

    # ----------------------------------------------------- tax years ----
    def get_or_create_tax_year(self, d: date) -> TaxYear:
        start, end, label = tax_year_bounds(d)
        ty = self.s.scalar(select(TaxYear).where(
            TaxYear.user_id == self.uid, TaxYear.start_date == start))
        if ty:
            return ty
        ty = TaxYear(user_id=self.uid, start_date=start, end_date=end, label=label)
        self.s.add(ty)
        self.s.flush()
        return ty

    def tax_years(self) -> list[TaxYear]:
        return list(self.s.scalars(select(TaxYear).where(
            TaxYear.user_id == self.uid).order_by(TaxYear.start_date.desc())).all())

    def get_tax_year(self, tyid: str) -> TaxYear | None:
        return self.s.scalar(select(TaxYear).where(
            TaxYear.id == tyid, TaxYear.user_id == self.uid))

    def get_tax_year_by_label(self, label: str) -> TaxYear | None:
        return self.s.scalar(select(TaxYear).where(
            TaxYear.user_id == self.uid, TaxYear.label == label))

    def current_tax_year(self) -> TaxYear:
        return self.get_or_create_tax_year(date.today())

    def close_tax_year(self, tyid: str, snapshot: dict, snapshot_hash: str) -> bool:
        ty = self.get_tax_year(tyid)
        if not ty or ty.status == "closed":
            return False
        ty.status = "closed"
        ty.closed_at = utcnow()
        ty.snapshot = snapshot
        ty.snapshot_hash = snapshot_hash
        self.s.execute(Entry.__table__.update().where(
            Entry.user_id == self.uid, Entry.tax_year_id == tyid).values(is_locked=True))
        self.s.flush()
        self.audit("tax_year", tyid, "close_year", {"hash": snapshot_hash})
        return True

    # ------------------------------------------------------- entries ----
    def create_entry(self, *, entry_type: str, entry_date: date, amount_minor: int,
                     category_id: str | None = None, notes: str | None = None,
                     vendor: str | None = None, student_id: str | None = None,
                     is_paid: bool | None = None) -> Entry:
        ty = self.get_or_create_tax_year(entry_date)
        e = Entry(user_id=self.uid, entry_type=entry_type, entry_date=entry_date,
                  amount_minor=amount_minor, category_id=category_id, notes=notes,
                  vendor=vendor, student_id=student_id, is_paid=is_paid,
                  tax_year_id=ty.id, is_locked=(ty.status == "closed"))
        self.s.add(e)
        self.s.flush()
        self.audit("entry", e.id, "create",
                   {"type": entry_type, "amount_minor": amount_minor})
        return e

    def get_entry(self, eid: str, include_deleted: bool = False) -> Entry | None:
        q = select(Entry).where(Entry.id == eid, Entry.user_id == self.uid)
        if not include_deleted:
            q = q.where(Entry.is_deleted.is_(False))
        return self.s.scalar(q)

    def update_entry(self, eid: str, **fields) -> tuple[Entry | None, str]:
        e = self.get_entry(eid)
        if not e:
            return None, "Entry not found."
        if e.is_locked:
            return None, "That tax year has been closed, so this entry is locked."
        allowed = {"entry_date", "amount_minor", "category_id", "notes", "vendor",
                   "student_id", "is_paid", "entry_type"}
        diff = {}
        for k, v in fields.items():
            if k in allowed and getattr(e, k) != v:
                diff[k] = v
                setattr(e, k, v)
        if "entry_date" in diff:  # may move tax years
            e.tax_year_id = self.get_or_create_tax_year(e.entry_date).id
        self.s.flush()
        if diff:
            self.audit("entry", eid, "update", diff)
        return e, "Saved."

    def soft_delete_entry(self, eid: str) -> Entry | None:
        e = self.get_entry(eid)
        if not e or e.is_locked:
            return None
        e.is_deleted = True
        e.deleted_at = utcnow()
        self.s.flush()
        self.audit("entry", eid, "delete", None)
        return e

    def restore_entry(self, eid: str) -> Entry | None:
        e = self.get_entry(eid, include_deleted=True)
        if not e or not e.is_deleted:
            return None
        e.is_deleted = False
        e.deleted_at = None
        self.s.flush()
        self.audit("entry", eid, "restore", None)
        return e

    def set_paid(self, eid: str, paid: bool) -> Entry | None:
        e = self.get_entry(eid)
        if not e or e.is_locked:
            return None
        e.is_paid = paid
        self.s.flush()
        self.audit("entry", eid, "update", {"is_paid": paid})
        return e

    def list_entries(self, *, tax_year_id: str | None = None, month: str | None = None,
                     entry_type: str | None = None, category_id: str | None = None,
                     student_id: str | None = None, include_deleted: bool = False,
                     limit: int | None = None) -> list[Entry]:
        q = select(Entry).where(Entry.user_id == self.uid)
        if not include_deleted:
            q = q.where(Entry.is_deleted.is_(False))
        if tax_year_id:
            q = q.where(Entry.tax_year_id == tax_year_id)
        if entry_type:
            q = q.where(Entry.entry_type == entry_type)
        if category_id:
            q = q.where(Entry.category_id == category_id)
        if student_id:
            q = q.where(Entry.student_id == student_id)
        if month:  # "YYYY-MM"
            y, m = (int(x) for x in month.split("-"))
            start = date(y, m, 1)
            end = date(y + (m == 12), (m % 12) + 1, 1)
            q = q.where(Entry.entry_date >= start, Entry.entry_date < end)
        q = q.order_by(Entry.entry_date.desc(), Entry.created_at.desc())
        if limit:
            q = q.limit(limit)
        return list(self.s.scalars(q).all())

    def recent_entries(self, limit: int = 8) -> list[Entry]:
        return self.list_entries(limit=limit)

    def months_in_year(self, tax_year_id: str) -> list[dict]:
        """Per-month roll-up for the ledger (newest first)."""
        rows = self.list_entries(tax_year_id=tax_year_id)
        buckets: dict[tuple[int, int], dict] = {}
        for e in rows:
            key = (e.entry_date.year, e.entry_date.month)
            b = buckets.setdefault(key, {"year": key[0], "month": key[1], "count": 0,
                                         "income_minor": 0, "expense_minor": 0})
            b["count"] += 1
            if e.entry_type == "income":
                b["income_minor"] += e.amount_minor
            else:
                b["expense_minor"] += e.amount_minor
        return [buckets[k] for k in sorted(buckets, reverse=True)]

    # ------------------------------------------------------ receipts ----
    def create_receipt(self, storage_key: str, content_type: str = "image/jpeg",
                       byte_size: int = 0, thumb_key: str | None = None,
                       entry_id: str | None = None) -> Receipt:
        r = Receipt(user_id=self.uid, storage_key=storage_key, thumb_key=thumb_key,
                    content_type=content_type, byte_size=byte_size, entry_id=entry_id)
        self.s.add(r)
        self.s.flush()
        return r

    def get_receipt(self, rid: str) -> Receipt | None:
        return self.s.scalar(select(Receipt).where(
            Receipt.id == rid, Receipt.user_id == self.uid))

    def link_receipt(self, rid: str, entry_id: str):
        r = self.get_receipt(rid)
        if r:
            r.entry_id = entry_id
            self.s.flush()

    def receipt_for_entry(self, entry_id: str) -> Receipt | None:
        return self.s.scalar(select(Receipt).where(
            Receipt.user_id == self.uid, Receipt.entry_id == entry_id))

    # ----------------------------------------- totals (tax + export) ----
    def gross_income_minor(self, tax_year_id: str) -> int:
        """Qualifying income = gross turnover before expenses (plan §7)."""
        return int(self.s.scalar(
            select(func.coalesce(func.sum(Entry.amount_minor), 0)).where(
                Entry.user_id == self.uid, Entry.tax_year_id == tax_year_id,
                Entry.entry_type == "income", Entry.is_deleted.is_(False))) or 0)

    def expense_total_minor(self, tax_year_id: str) -> int:
        return int(self.s.scalar(
            select(func.coalesce(func.sum(Entry.amount_minor), 0)).where(
                Entry.user_id == self.uid, Entry.tax_year_id == tax_year_id,
                Entry.entry_type == "expense", Entry.is_deleted.is_(False))) or 0)

    def category_totals(self, tax_year_id: str) -> list[dict]:
        out = []
        for c in self.categories(include_hidden=True):
            total = int(self.s.scalar(
                select(func.coalesce(func.sum(Entry.amount_minor), 0)).where(
                    Entry.user_id == self.uid, Entry.tax_year_id == tax_year_id,
                    Entry.category_id == c.id, Entry.is_deleted.is_(False))) or 0)
            if total:
                box = SA103_BY_CODE.get(c.sa103_code, {})
                out.append({"category": c, "label": c.label, "kind": c.kind,
                            "total_minor": total, "box_number": box.get("box_number", "31"),
                            "box_label": box.get("label", "")})
        return out

    def totals_by_box(self, tax_year_id: str) -> dict[str, dict]:
        agg: dict[str, dict] = {}
        for row in self.category_totals(tax_year_id):
            b = agg.setdefault(row["box_number"], {
                "box_number": row["box_number"], "box_label": row["box_label"],
                "kind": row["kind"], "total_minor": 0})
            b["total_minor"] += row["total_minor"]
        return dict(sorted(agg.items(), key=lambda kv: kv[0]))

    def unpaid_income(self, tax_year_id: str | None = None) -> list[Entry]:
        q = select(Entry).where(
            Entry.user_id == self.uid, Entry.entry_type == "income",
            Entry.is_paid.is_(False), Entry.is_deleted.is_(False))
        if tax_year_id:
            q = q.where(Entry.tax_year_id == tax_year_id)
        return list(self.s.scalars(q.order_by(Entry.entry_date.desc())).all())

    # ----------------------------------------------------- reminders ----
    def reminder_sent(self, dedupe_key: str) -> bool:
        return self.s.scalar(select(func.count()).select_from(ReminderLog).where(
            ReminderLog.dedupe_key == dedupe_key)) > 0

    def log_reminder(self, dedupe_key: str, obligation: str, milestone: str,
                     channel: str) -> bool:
        if self.reminder_sent(dedupe_key):
            return False
        self.s.add(ReminderLog(user_id=self.uid, dedupe_key=dedupe_key,
                               obligation=obligation, milestone=milestone, channel=channel))
        self.s.flush()
        return True

    # ------------------------------------------------------- repeats ----
    def list_repeats(self, *, include_archived: bool = False) -> list[Repeat]:
        q = select(Repeat).where(Repeat.user_id == self.uid, Repeat.is_deleted.is_(False))
        if not include_archived:
            q = q.where(Repeat.is_archived.is_(False))
        return list(self.s.scalars(
            q.order_by(Repeat.display_order, Repeat.created_at)).all())

    def get_repeat(self, rid: str) -> Repeat | None:
        return self.s.scalar(select(Repeat).where(
            Repeat.id == rid, Repeat.user_id == self.uid, Repeat.is_deleted.is_(False)))

    def create_repeat(self, *, entry_type: str, label: str = "",
                      amount_minor: int | None = None, category_id: str | None = None,
                      student_id: str | None = None, vendor: str | None = None,
                      notes: str | None = None, default_paid: bool = True,
                      cadence: str = "weekly", weekday: int | None = None) -> Repeat:
        order = (self.s.scalar(select(func.coalesce(func.max(Repeat.display_order), 0))
                               .where(Repeat.user_id == self.uid)) or 0) + 1
        r = Repeat(user_id=self.uid, entry_type=entry_type, label=(label or "").strip(),
                   amount_minor=amount_minor, category_id=category_id,
                   student_id=student_id, vendor=(vendor or None),
                   notes=(notes or None), default_paid=default_paid,
                   cadence=cadence, weekday=weekday, display_order=order)
        self.s.add(r)
        self.s.flush()
        self.audit("repeat", r.id, "create", {"label": r.label or vendor or ""})
        return r

    def update_repeat(self, rid: str, **fields) -> Repeat | None:
        r = self.get_repeat(rid)
        if not r:
            return None
        allowed = {"label", "amount_minor", "category_id", "student_id", "vendor",
                   "notes", "default_paid", "cadence", "weekday", "display_order",
                   "is_archived"}
        diff = {}
        for k, v in fields.items():
            if k in allowed and getattr(r, k) != v:
                diff[k] = v
                setattr(r, k, v)
        self.s.flush()
        if diff:
            self.audit("repeat", rid, "update", diff)
        return r

    def archive_repeat(self, rid: str) -> Repeat | None:
        r = self.get_repeat(rid)
        if not r:
            return None
        r.is_archived = True
        self.s.flush()
        self.audit("repeat", rid, "archive", None)
        return r

    def restore_repeat(self, rid: str) -> Repeat | None:
        r = self.get_repeat(rid)
        if not r:
            return None
        r.is_archived = False
        self.s.flush()
        self.audit("repeat", rid, "restore", None)
        return r

    def log_repeat(self, rid: str, *, on_date: date | None = None,
                   amount_minor: int | None = None) -> Entry | None:
        """Tap-to-log: create a real Entry from a repeat and bump its stats.

        Returns None if the repeat is missing or has no amount to use (the route
        then sends the user to a prefilled form to type one).
        """
        r = self.get_repeat(rid)
        if not r:
            return None
        amt = amount_minor if amount_minor is not None else r.amount_minor
        if amt is None:
            return None
        when = on_date or date.today()
        e = self.create_entry(
            entry_type=r.entry_type, entry_date=when, amount_minor=amt,
            category_id=r.category_id, vendor=r.vendor, student_id=r.student_id,
            notes=r.notes,
            is_paid=(r.default_paid if r.entry_type == "income" else None))
        r.times_logged = (r.times_logged or 0) + 1
        r.last_logged_on = when
        self.s.flush()
        self.audit("repeat", rid, "log", {"entry_id": e.id, "amount_minor": amt})
        return e

    def student_ids_with_repeat(self) -> set[str]:
        rows = self.s.execute(select(Repeat.student_id).where(
            Repeat.user_id == self.uid, Repeat.is_deleted.is_(False),
            Repeat.student_id.is_not(None))).all()
        return {r[0] for r in rows}

    # ---- suggestion dismissals ----
    def dismiss_suggestion(self, kind: str, ref: str) -> None:
        exists = self.s.scalar(select(RepeatDismissal).where(
            RepeatDismissal.user_id == self.uid, RepeatDismissal.kind == kind,
            RepeatDismissal.ref == ref))
        if not exists:
            self.s.add(RepeatDismissal(user_id=self.uid, kind=kind, ref=ref))
            self.s.flush()

    def dismissed_refs(self, kind: str) -> set[str]:
        rows = self.s.execute(select(RepeatDismissal.ref).where(
            RepeatDismissal.user_id == self.uid, RepeatDismissal.kind == kind)).all()
        return {r[0] for r in rows}
