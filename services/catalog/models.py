from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def now():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid4())


class Embedding(TypeDecorator):
    """Actual pgvector in production; JSON only on isolated SQLite databases."""
    impl = JSON(none_as_null=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(Vector(384) if dialect.name == "postgresql" else JSON(none_as_null=True))

    def process_bind_param(self, value, dialect):
        return None if value is None else [float(x) for x in value]


class Base(DeclarativeBase):
    pass


class RestaurantRow(Base):
    __tablename__ = "restaurants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    cuisines: Mapped[list] = mapped_column(JSON, default=list)
    phone: Mapped[str | None] = mapped_column(String(50))
    website: Mapped[str | None] = mapped_column(String(500))
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/Moscow")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    active_menu_version_id: Mapped[str | None] = mapped_column(String(36))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class DishRow(Base):
    __tablename__ = "dishes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)


class ImportRow(Base):
    __tablename__ = "menu_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id"), index=True)
    source_kind: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100))
    file_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    error: Mapped[dict | None] = mapped_column(JSON)
    published_menu_version_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    draft_items: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    # A single-process worker recovers interrupted imports on startup.
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class MenuVersionRow(Base):
    __tablename__ = "menu_versions"
    __table_args__ = (UniqueConstraint("restaurant_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id"), index=True)
    import_id: Mapped[str] = mapped_column(ForeignKey("menu_imports.id"), unique=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="published")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")


class MenuItemRow(Base):
    __tablename__ = "menu_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dish_id: Mapped[str] = mapped_column(ForeignKey("dishes.id"), index=True)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id"), index=True)
    menu_version_id: Mapped[str] = mapped_column(ForeignKey("menu_versions.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    ingredients: Mapped[list] = mapped_column(JSON, default=list)
    ingredients_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    portion_weight_g: Mapped[float | None] = mapped_column(Float)
    allergen_info_status: Mapped[str] = mapped_column(String(20), default="unknown")
    allergens: Mapped[list] = mapped_column(JSON, default=list)
    nutrition: Mapped[dict] = mapped_column(JSON)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding: Mapped[list | None] = mapped_column(Embedding)
    embedding_model: Mapped[str | None] = mapped_column(String(200))


class EvidenceRow(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id"), index=True)
    import_id: Mapped[str | None] = mapped_column(ForeignKey("menu_imports.id"), index=True)
    menu_item_id: Mapped[str | None] = mapped_column(ForeignKey("menu_items.id"), index=True)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_reference: Mapped[str] = mapped_column(String(500))
    page_number: Mapped[int | None] = mapped_column(Integer)
    fragment_text: Mapped[str] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReviewRow(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id"), index=True)
    rating: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(500))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"))
    embedding: Mapped[list | None] = mapped_column(Embedding)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
