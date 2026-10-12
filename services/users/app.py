import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from pwdlib import PasswordHash
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.common.db import make_database
from services.common.errors import install_error_handlers
from services.common.openapi import install_gateway_security_schema
from services.common.security import Principal, auth_secret, create_access_token, current_user, require_internal

from .models import (
    Account, AllergyRow, Base, DiaryEntryRow, DiarySnapshotRow, FeedbackRow, ProfileRow, utc_now,
)
from .nutrition import NUTRIENTS, aggregate_nutrition, calculate_price, utc_datetime, utc_day_bounds
from .schemas import (
    AllergenDeclaration, AuthResponse, DailySummary, DiaryEntry, DiaryEntryCreate,
    DiaryEntryPatch, DiaryPage, DiarySnapshot, Feedback, FeedbackCreate, FeedbackPage,
    LoginRequest, MealCalculateRequest, MealCalculation, NutritionFacts, Profile,
    ProfilePatch, RegisterRequest, User, UserContexts,
)

password_hash = PasswordHash.recommended()
# Verify a real hash on unknown-account login too, avoiding the cheap early-exit path.
DUMMY_PASSWORD_HASH = password_hash.hash("not-an-account-password")


class CatalogMenuItem(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    id: UUID
    dish_id: UUID
    restaurant_id: UUID
    menu_version_id: UUID
    name: str
    ingredients: list[str]
    allergen_info_status: str
    allergens: list[AllergenDeclaration]
    portion_weight_g: Decimal | None
    price_minor: int
    currency: str
    nutrition: NutritionFacts


def _persistable(value):
    """Keep exact decimal quantities in JSON instead of binary floats."""
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return utc_datetime(value).isoformat()
    if isinstance(value, dict):
        return {key: _persistable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_persistable(item) for item in value]
    return value


def _request_hash(body: BaseModel) -> str:
    serialized = json.dumps(_persistable(body.model_dump()), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _profile(row: ProfileRow) -> Profile:
    return Profile(
        user_id=row.user_id, timezone=row.timezone,
        favorite_cuisines=row.favorite_cuisines, disliked_ingredients=row.disliked_ingredients,
        spice_preference=row.spice_preference,
        allergies=[{"code": allergy.code, "note": allergy.note} for allergy in row.allergies],
        daily_calories_target=int(row.daily_calories_target) if row.daily_calories_target is not None else None,
        daily_protein_target_g=row.daily_protein_target_g,
        daily_fat_target_g=row.daily_fat_target_g, daily_carbs_target_g=row.daily_carbs_target_g,
        default_budget_minor=row.default_budget_minor, currency=row.currency,
        proactive_enabled=row.proactive_enabled, updated_at=utc_datetime(row.updated_at),
    )


def _diary(row: DiaryEntryRow) -> DiaryEntry:
    snapshot = DiarySnapshot.model_validate(row.snapshot.data)
    return DiaryEntry(
        id=row.id, user_id=row.user_id, kind=row.kind,
        consumed_at=utc_datetime(row.consumed_at), portions=row.portions, note=row.note,
        snapshot=snapshot,
        consumed_nutrition=aggregate_nutrition([(snapshot.nutrition_per_portion, row.portions)]),
        created_at=utc_datetime(row.created_at), updated_at=utc_datetime(row.updated_at),
    )


def _feedback(row: FeedbackRow) -> Feedback:
    return Feedback(
        id=row.id, user_id=row.user_id, dish_id=row.dish_id,
        recommendation_id=row.recommendation_id, rating=row.rating,
        comment=row.comment, updated_at=utc_datetime(row.updated_at),
    )


def _auth(row: Account) -> AuthResponse:
    lifetime = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "3600"))
    return AuthResponse(
        access_token=create_access_token(Principal(id=row.id, role=row.role), lifetime_seconds=lifetime),
        expires_in=lifetime,
        user=User(id=row.id, email=row.email, role=row.role, created_at=utc_datetime(row.created_at)),
    )


def _require_auth_configuration():
    try:
        auth_secret()
    except RuntimeError as exc:
        raise HTTPException(503, "Authentication is not configured") from exc


def create_app() -> FastAPI:
    fallback = str(Path(os.getenv("DATA_DIR", "work/data")) / "users.db")
    engine, session_factory = make_database("USERS_DATABASE_URL", fallback)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        Base.metadata.create_all(engine)
        demo_password = os.getenv("DEMO_ADMIN_PASSWORD")
        if demo_password:
            if len(demo_password) < 8 or len(demo_password) > 128:
                raise RuntimeError("DEMO_ADMIN_PASSWORD must have 8 to 128 characters")
            demo_email = RegisterRequest(
                email=os.getenv("DEMO_ADMIN_EMAIL", "admin@example.test"), password=demo_password,
            ).email.lower()
            with session_factory() as db:
                if db.scalar(select(Account).where(Account.email == demo_email)) is None:
                    account = Account(email=demo_email, password_hash=password_hash.hash(demo_password), role="admin")
                    db.add(account)
                    db.flush()
                    db.add(ProfileRow(user_id=account.id))
                    db.commit()
        yield
        engine.dispose()

    application = FastAPI(
        title="ГастроГид — User & Nutrition Service", version="0.1.0", lifespan=lifespan,
        description="Accounts, explicit preferences, immutable food snapshots and exact nutrition arithmetic.",
    )
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.catalog_transport = None
    application.state.recommendations_transport = None
    install_error_handlers(application)
    install_gateway_security_schema(application)

    def session():
        with session_factory() as db:
            yield db

    Database = Annotated[Session, Depends(session)]
    Authenticated = Annotated[Principal, Depends(current_user)]
    Authorization = Annotated[str, Header()]

    def own_profile(db: Session, principal: Principal) -> ProfileRow:
        # Reject a valid token referring to an account absent from this database.
        row = db.get(ProfileRow, str(principal.id))
        if row is None:
            raise HTTPException(401, "Account no longer exists")
        return row

    def own_entry(db: Session, principal: Principal, entry_id: UUID) -> DiaryEntryRow:
        row = db.scalar(select(DiaryEntryRow).where(
            DiaryEntryRow.id == str(entry_id), DiaryEntryRow.user_id == str(principal.id),
            DiaryEntryRow.deleted_at.is_(None),
        ))
        if row is None:
            raise HTTPException(404, "Diary entry not found")
        return row

    async def service_get(service: str, path: str, authorization: str) -> dict:
        url = os.getenv(f"{service.upper()}_URL", f"http://{service}:" + ("8001" if service == "catalog" else "8003"))
        transport = getattr(application.state, f"{service}_transport", None)
        try:
            async with httpx.AsyncClient(timeout=10, transport=transport) as client:
                response = await client.get(url.rstrip("/") + path, headers={"Authorization": authorization})
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"{service.title()} service unavailable") from exc
        if response.status_code == 404:
            raise HTTPException(404, "Referenced resource not found")
        if response.status_code == 401:
            raise HTTPException(401, "Authentication rejected by upstream service")
        if response.status_code != 200:
            raise HTTPException(503, f"{service.title()} service returned an error")
        try:
            data = response.json()
        except ValueError as exc:
            raise HTTPException(503, f"{service.title()} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise HTTPException(503, f"{service.title()} returned invalid data")
        return data

    async def catalog_item(item_id: UUID, authorization: str) -> CatalogMenuItem:
        data = await service_get("catalog", f"/api/v1/menu-items/{item_id}", authorization)
        try:
            item = CatalogMenuItem.model_validate(data)
            if item.id != item_id or item.price_minor < 0:
                raise ValueError("Invalid menu item identity or price")
            item_snapshot(item)
            return item
        except (ValidationError, ValueError) as exc:
            raise HTTPException(503, "Catalog returned inconsistent menu item data") from exc

    def item_snapshot(item: CatalogMenuItem) -> DiarySnapshot:
        return DiarySnapshot(
            name=item.name, menu_item_id=item.id, dish_id=item.dish_id,
            restaurant_id=item.restaurant_id, menu_version_id=item.menu_version_id,
            ingredients=item.ingredients, allergen_info_status=item.allergen_info_status,
            allergens=item.allergens, portion_weight_g=item.portion_weight_g,
            price_minor=item.price_minor, currency=item.currency,
            nutrition_per_portion=item.nutrition,
        )

    def entries_for_day(db: Session, principal: Principal, day: date, profile: ProfileRow):
        start, end = utc_day_bounds(day, profile.timezone)
        return select(DiaryEntryRow).where(
            DiaryEntryRow.user_id == str(principal.id), DiaryEntryRow.deleted_at.is_(None),
            DiaryEntryRow.consumed_at >= start, DiaryEntryRow.consumed_at < end,
        )

    @application.get("/health", tags=["Operations"])
    def health():
        return {"status": "ok", "service": "users"}

    @application.get("/ready", tags=["Operations"])
    def ready(db: Database):
        db.execute(text("SELECT 1"))
        _require_auth_configuration()
        return {"status": "ready", "service": "users"}

    @application.post("/api/v1/auth/register", response_model=AuthResponse, status_code=201, tags=["Auth"])
    def register(body: RegisterRequest, db: Database):
        _require_auth_configuration()
        email = body.email.lower()
        account = Account(email=email, password_hash=password_hash.hash(body.password), role="user")
        db.add(account)
        try:
            db.flush()
            db.add(ProfileRow(user_id=account.id))
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "Email already registered") from exc
        return _auth(account)

    @application.post("/api/v1/auth/login", response_model=AuthResponse, tags=["Auth"])
    def login(body: LoginRequest, db: Database):
        _require_auth_configuration()
        account = db.scalar(select(Account).where(Account.email == body.email.lower()))
        try:
            valid = password_hash.verify(body.password, account.password_hash if account else DUMMY_PASSWORD_HASH)
        except Exception:
            valid = False
        if account is None or not valid:
            raise HTTPException(401, "Invalid email or password")
        return _auth(account)

    @application.get("/api/v1/me/profile", response_model=Profile, tags=["Profile"])
    def get_profile(db: Database, principal: Authenticated):
        return _profile(own_profile(db, principal))

    @application.patch("/api/v1/me/profile", response_model=Profile, tags=["Profile"])
    def patch_profile(body: ProfilePatch, db: Database, principal: Authenticated):
        row = own_profile(db, principal)
        values = body.model_dump(exclude_unset=True)
        if "allergies" in values:
            # Updating existing rows avoids transient primary-key conflicts when replacing the list.
            requested = {item["code"]: item.get("note") for item in values.pop("allergies")}
            existing = {item.code: item for item in row.allergies}
            row.allergies = [
                existing.get(code) or AllergyRow(code=code, note=note)
                for code, note in requested.items()
            ]
            for item in row.allergies:
                item.note = requested[item.code]
        for name, value in values.items():
            setattr(row, name, value)
        row.updated_at = utc_now()
        db.commit()
        return _profile(row)

    @application.post("/api/v1/nutrition/calculate", response_model=MealCalculation, tags=["Nutrition"])
    async def calculate(body: MealCalculateRequest, db: Database, principal: Authenticated, authorization: Authorization):
        own_profile(db, principal)
        fetched = {}
        for requested in body.items:
            if requested.menu_item_id not in fetched:
                fetched[requested.menu_item_id] = await catalog_item(requested.menu_item_id, authorization)
        items = [(fetched[requested.menu_item_id], requested.portions) for requested in body.items]
        first = items[0][0]
        if any((item.restaurant_id, item.menu_version_id, item.currency) !=
               (first.restaurant_id, first.menu_version_id, first.currency) for item, _ in items):
            raise HTTPException(422, "A meal must use one restaurant, one menu version and one currency")
        totals = aggregate_nutrition((item.nutrition, portions) for item, portions in items)
        warnings = []
        if len(totals.complete_fields) < len(NUTRIENTS):
            warnings.append("Nutrition is incomplete; unknown values are not included as zero.")
        if "estimated" in totals.sources:
            warnings.append("Some nutrition values are estimates.")
        return MealCalculation(
            items=body.items, price_minor=calculate_price((item.price_minor, portions) for item, portions in items),
            currency=first.currency, nutrition=totals, warnings=warnings,
        )

    @application.get("/api/v1/me/diary", response_model=DiaryPage, tags=["Diary"])
    def list_diary(db: Database, principal: Authenticated, date: date,
                   limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        profile = own_profile(db, principal)
        query = entries_for_day(db, principal, date, profile)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(query.order_by(DiaryEntryRow.consumed_at, DiaryEntryRow.id).limit(limit).offset(offset)).all()
        return DiaryPage(items=[_diary(row) for row in rows], total=total, limit=limit, offset=offset)

    @application.post("/api/v1/me/diary", response_model=DiaryEntry, status_code=201, tags=["Diary"])
    async def create_diary(body: DiaryEntryCreate, db: Database, principal: Authenticated, authorization: Authorization,
                           idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
        own_profile(db, principal)
        if not idempotency_key.strip() or not idempotency_key.isascii() or any(c.isspace() for c in idempotency_key):
            raise HTTPException(422, "Idempotency-Key must be 1 to 128 ASCII characters without whitespace")
        fingerprint = _request_hash(body)
        query = select(DiaryEntryRow).where(
            DiaryEntryRow.user_id == str(principal.id), DiaryEntryRow.idempotency_key == idempotency_key,
        )

        def replay(row: DiaryEntryRow):
            if row.deleted_at is not None:
                raise HTTPException(409, "This operation was deleted; use a new Idempotency-Key")
            if row.request_hash != fingerprint:
                raise HTTPException(409, "Idempotency-Key was used for a different request")
            return _diary(row)

        existing = db.scalar(query)
        if existing is not None:
            return replay(existing)
        if body.kind == "menu_item":
            snapshot = item_snapshot(await catalog_item(body.menu_item_id, authorization))
        else:
            snapshot = DiarySnapshot(
                name=body.name, menu_item_id=None, dish_id=None, restaurant_id=None,
                menu_version_id=None, ingredients=[], allergen_info_status="unknown", allergens=[],
                portion_weight_g=None, price_minor=None, currency=None,
                nutrition_per_portion=NutritionFacts(
                    **body.nutrition_per_portion.model_dump(), source="user_entered", evidence_ids=[],
                ),
            )
        row = DiaryEntryRow(
            user_id=str(principal.id), kind=body.kind, consumed_at=utc_datetime(body.consumed_at),
            portions=body.portions, note=body.note, idempotency_key=idempotency_key, request_hash=fingerprint,
            snapshot=DiarySnapshotRow(data=_persistable(snapshot.model_dump())),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # Concurrent retries converge on the one row guaranteed by the unique constraint.
            existing = db.scalar(query)
            if existing is None:
                raise HTTPException(409, "Concurrent diary operation; retry with the same key")
            return replay(existing)
        return _diary(row)

    @application.patch("/api/v1/me/diary/{entry_id}", response_model=DiaryEntry, tags=["Diary"])
    def patch_diary(entry_id: UUID, body: DiaryEntryPatch, db: Database, principal: Authenticated):
        own_profile(db, principal)
        row = own_entry(db, principal, entry_id)
        for name, value in body.model_dump(exclude_unset=True).items():
            setattr(row, name, utc_datetime(value) if name == "consumed_at" else value)
        row.updated_at = utc_now()
        db.commit()
        return _diary(row)

    @application.delete("/api/v1/me/diary/{entry_id}", status_code=204, tags=["Diary"])
    def delete_diary(entry_id: UUID, db: Database, principal: Authenticated):
        own_profile(db, principal)
        row = own_entry(db, principal, entry_id)
        row.deleted_at = utc_now()
        row.updated_at = row.deleted_at
        db.commit()
        return Response(status_code=204)

    @application.get("/api/v1/me/daily-summary", response_model=DailySummary, tags=["Nutrition"])
    def daily_summary(db: Database, principal: Authenticated, date: date):
        profile = own_profile(db, principal)
        rows = db.scalars(entries_for_day(db, principal, date, profile)).all()
        totals = aggregate_nutrition(
            (DiarySnapshot.model_validate(row.snapshot.data).nutrition_per_portion, row.portions) for row in rows
        )
        target_fields = ("daily_calories_target", "daily_protein_target_g", "daily_fat_target_g", "daily_carbs_target_g")
        targets = {nutrient: getattr(profile, field) for nutrient, field in zip(NUTRIENTS, target_fields)}
        remaining = {
            nutrient: Decimal(targets[nutrient]) - getattr(totals, nutrient)
            if targets[nutrient] is not None and getattr(totals, nutrient) is not None else None
            for nutrient in NUTRIENTS
        }
        warnings = []
        if len(totals.complete_fields) < len(NUTRIENTS):
            warnings.append("Daily totals are incomplete; known_subtotals show only documented values.")
        if "estimated" in totals.sources:
            warnings.append("Some nutrition values are estimates.")
        return DailySummary(
            date=date, timezone=profile.timezone, entry_count=len(rows), nutrition=totals,
            targets=targets, remaining=remaining, warnings=warnings,
        )

    @application.get("/api/v1/me/feedback", response_model=FeedbackPage, tags=["Feedback"])
    def list_feedback(db: Database, principal: Authenticated, limit: int = Query(default=100, ge=1, le=500)):
        own_profile(db, principal)
        rows = db.scalars(select(FeedbackRow).where(FeedbackRow.user_id == str(principal.id))
                          .order_by(FeedbackRow.updated_at.desc()).limit(limit)).all()
        return FeedbackPage(items=[_feedback(row) for row in rows])

    @application.post("/api/v1/me/feedback", response_model=Feedback, tags=["Feedback"])
    async def set_feedback(body: FeedbackCreate, db: Database, principal: Authenticated, authorization: Authorization):
        own_profile(db, principal)
        dish = await service_get("catalog", f"/api/v1/dishes/{body.dish_id}", authorization)
        if dish.get("id") != str(body.dish_id):
            raise HTTPException(503, "Catalog returned inconsistent dish identity")
        if body.recommendation_id is not None:
            # The recommendations endpoint only exposes runs belonging to the caller.
            recommendation = await service_get("recommendations", f"/api/v1/recommendations/{body.recommendation_id}", authorization)
            if recommendation.get("id") != str(body.recommendation_id):
                raise HTTPException(503, "Recommendations returned inconsistent request identity")
        query = select(FeedbackRow).where(FeedbackRow.user_id == str(principal.id), FeedbackRow.dish_id == str(body.dish_id))
        row = db.scalar(query)
        if row is None:
            row = FeedbackRow(user_id=str(principal.id), dish_id=str(body.dish_id))
            db.add(row)
        row.rating, row.comment = body.rating, body.comment
        row.recommendation_id = str(body.recommendation_id) if body.recommendation_id else None
        row.updated_at = utc_now()
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.scalar(query)
            if row is None:
                raise HTTPException(409, "Concurrent feedback operation; retry")
            row.rating, row.comment = body.rating, body.comment
            row.recommendation_id = str(body.recommendation_id) if body.recommendation_id else None
            row.updated_at = utc_now()
            db.commit()
        return _feedback(row)

    @application.get("/api/v1/internal/users-contexts", response_model=UserContexts,
                     dependencies=[Depends(require_internal)], tags=["Internal"])
    def internal_user_contexts(db: Database):
        rows = db.execute(select(Account, ProfileRow).join(ProfileRow, Account.id == ProfileRow.user_id)
                          .where(ProfileRow.proactive_enabled.is_(True))).all()
        return UserContexts(items=[
            {"user_id": account.id, "profile": _profile(profile),
             "jwt": create_access_token(Principal(id=account.id, role=account.role), lifetime_seconds=300)}
            for account, profile in rows
        ])

    return application


app = create_app()
