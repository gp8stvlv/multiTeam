from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProfileRow(Base):
    __tablename__ = "user_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    favorite_cuisines: Mapped[list] = mapped_column(JSON, default=list)
    disliked_ingredients: Mapped[list] = mapped_column(JSON, default=list)
    spice_preference: Mapped[str] = mapped_column(String(16), default="any")
    daily_calories_target: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    daily_protein_target_g: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    daily_fat_target_g: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    daily_carbs_target_g: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    default_budget_minor: Mapped[int | None] = mapped_column(nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    proactive_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    allergies: Mapped[list["AllergyRow"]] = relationship(
        cascade="all, delete-orphan", order_by="AllergyRow.code", lazy="selectin"
    )


class AllergyRow(Base):
    __tablename__ = "user_allergies"
    user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.user_id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DiaryEntryRow(Base):
    __tablename__ = "diary_entries"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    portions: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    snapshot: Mapped["DiarySnapshotRow"] = relationship(
        cascade="all, delete-orphan", uselist=False, lazy="joined"
    )


class DiarySnapshotRow(Base):
    __tablename__ = "diary_snapshots"
    entry_id: Mapped[str] = mapped_column(ForeignKey("diary_entries.id"), primary_key=True)
    # Decimal strings retain exact source quantities in immutable snapshots.
    data: Mapped[dict] = mapped_column(JSON)


class FeedbackRow(Base):
    __tablename__ = "dish_feedback"
    __table_args__ = (UniqueConstraint("user_id", "dish_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    dish_id: Mapped[str] = mapped_column(String(36), index=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rating: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
