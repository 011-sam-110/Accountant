"""SQLAlchemy 2.0 ORM models — the shared data contract.

Portable across sqlite (local dev) and Postgres (Neon, prod). Postgres-only
hardening (citext, RLS, BEFORE UPDATE/DELETE triggers for closed-year
immutability) is layered on in migrations; the app layer enforces the same
rules so behaviour is identical locally.

Money is BIGINT pennies. Tenant tables carry `user_id` and are always queried
through the scoped Repo (see db.py) — never an unscoped query.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .util import new_id, utcnow


class Base(DeclarativeBase):
    pass


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    recovery_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    sms_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_provider: Mapped[str] = mapped_column(String(20), default="otp")
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Sa103Box(Base):
    """Global, seeded once, immutable. Enum-as-data so HMRC changes are a seed."""
    __tablename__ = "sa103_box"

    code: Mapped[str] = mapped_column(String(48), primary_key=True)
    box_number: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(10))  # income | expense
    label: Mapped[str] = mapped_column(String(160))


class Category(Base):
    """Per-user visible layer. label is cosmetic; sa103_code keeps exports correct."""
    __tablename__ = "category"
    __table_args__ = (Index("ix_category_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    sa103_code: Mapped[str] = mapped_column(ForeignKey("sa103_box.code"))
    label: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10))  # income | expense
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    box: Mapped["Sa103Box"] = relationship(lazy="joined")


class Student(Base):
    __tablename__ = "student"
    __table_args__ = (Index("ix_student_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)


class TaxYear(Base):
    __tablename__ = "tax_year"
    __table_args__ = (
        UniqueConstraint("user_id", "start_date", name="uq_taxyear_user_start"),
        Index("ix_taxyear_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)   # 6 Apr
    end_date: Mapped[date] = mapped_column(Date)     # 5 Apr
    label: Mapped[str] = mapped_column(String(12))   # "2026/27"
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Entry(Base):
    """The core transaction. is_deleted = soft delete (genuine Undo)."""
    __tablename__ = "entry"
    __table_args__ = (
        Index("ix_entry_user_date", "user_id", "entry_date"),
        Index("ix_entry_user_cat", "user_id", "category_id"),
        Index("ix_entry_user_year", "user_id", "tax_year_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    entry_type: Mapped[str] = mapped_column(String(10))  # income | expense
    entry_date: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    category_id: Mapped[str | None] = mapped_column(ForeignKey("category.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(160), nullable=True)
    student_id: Mapped[str | None] = mapped_column(ForeignKey("student.id"), nullable=True)
    is_paid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # income only
    tax_year_id: Mapped[str | None] = mapped_column(ForeignKey("tax_year.id"), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    category: Mapped["Category | None"] = relationship(lazy="joined")
    student: Mapped["Student | None"] = relationship(lazy="joined")
    receipt: Mapped["Receipt | None"] = relationship(back_populates="entry", uselist=False)


class Receipt(Base):
    """Stores the storage KEY, never a public URL. Access via short-lived links."""
    __tablename__ = "receipt"
    __table_args__ = (Index("ix_receipt_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    entry_id: Mapped[str | None] = mapped_column(ForeignKey("entry.id"), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(255))
    thumb_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    entry: Mapped["Entry | None"] = relationship(back_populates="receipt")


class OtpCode(Base):
    """Single-use, hashed-at-rest login/recovery code."""
    __tablename__ = "otp_code"
    __table_args__ = (Index("ix_otp_email", "email"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Session(Base):
    """Server-side handle for revocation ('log out everywhere')."""
    __tablename__ = "session"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ReminderLog(Base):
    """Idempotent send ledger. dedupe_key = user:obligation:milestone."""
    __tablename__ = "reminder_log"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_reminder_dedupe"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(160))
    obligation: Mapped[str] = mapped_column(String(40))
    milestone: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(10))  # email | sms
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base):
    """Append-only honest record trail."""
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(20))
    diff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
