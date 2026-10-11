from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Point(Model):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Near(Model):
    point: Point
    radius_m: int = Field(ge=100, le=100000)


class RestaurantCreate(Model):
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    location: Point
    cuisines: list[str] = Field(default_factory=list, max_length=30)
    phone: str | None = Field(default=None, max_length=50)
    website: str | None = Field(default=None, max_length=500)
    timezone: str = "Europe/Moscow"
    active: bool = True

    @model_validator(mode="after")
    def valid_timezone(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("A valid IANA timezone is required") from exc
        return self


class RestaurantPatch(Model):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, min_length=1, max_length=500)
    location: Point | None = None
    cuisines: list[str] | None = Field(default=None, max_length=30)
    phone: str | None = Field(default=None, max_length=50)
    website: str | None = Field(default=None, max_length=500)
    timezone: str | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def not_empty(self):
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        for key in self.model_fields_set - {"phone", "website"}:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        if self.timezone is not None:
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("A valid IANA timezone is required") from exc
        return self


class Restaurant(RestaurantCreate):
    id: UUID
    rating: float | None = None
    review_count: int = 0
    distance_m: float | None = None
    active_menu_version_id: UUID | None = None
    updated_at: datetime
    is_demo: bool = False


class RestaurantPage(Model):
    items: list[Restaurant]
    total: int
    limit: int
    offset: int


class Review(Model):
    id: UUID
    restaurant_id: UUID
    rating: float = Field(ge=1, le=5)
    text: str
    source: str
    source_url: str | None = None
    reviewed_at: datetime
    is_demo: bool
    evidence_id: UUID | None = None


class ReviewPage(Model):
    items: list[Review]
    total: int
    limit: int
    offset: int


class Evidence(Model):
    id: UUID
    restaurant_id: UUID
    menu_item_id: UUID | None = None
    source_kind: Literal["html", "pdf", "image", "review", "nutrition_reference"]
    source_reference: str
    page_number: int | None = None
    fragment_text: str
    updated_at: datetime
    is_demo: bool = False


class NutritionFacts(Model):
    calories_kcal: float | None = Field(default=None, ge=0, le=100000)
    protein_g: float | None = Field(default=None, ge=0, le=100000)
    fat_g: float | None = Field(default=None, ge=0, le=100000)
    carbs_g: float | None = Field(default=None, ge=0, le=100000)
    basis: Literal["per_portion"] = "per_portion"
    source: Literal["restaurant_declared", "estimated", "user_entered", "unknown"] = "unknown"
    method: str | None = Field(default=None, max_length=2000)
    evidence_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def truthful(self):
        values = [self.calories_kcal, self.protein_g, self.fat_g, self.carbs_g]
        if self.source == "unknown" and any(x is not None for x in values):
            raise ValueError("Unknown nutrition must have null values")
        if self.source == "estimated" and not self.method:
            raise ValueError("Estimated nutrition requires an explicit method")
        if self.source == "restaurant_declared" and any(x is not None for x in values) and not self.evidence_ids:
            raise ValueError("Restaurant declarations require source evidence")
        return self


class AllergenDeclaration(Model):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    status: Literal["contains", "may_contain", "declared_absent"]
    evidence_id: UUID | None = None


class Dish(Model):
    id: UUID
    restaurant_id: UUID
    name: str
    description: str | None = None


class DraftItem(Model):
    draft_key: str = Field(min_length=1, max_length=100)
    existing_dish_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    ingredients: list[str] = Field(default_factory=list, max_length=100)
    ingredients_complete: bool = False
    tags: list[str] = Field(default_factory=list, max_length=50)
    price_minor: int | None = Field(default=None, ge=0, le=100000000)
    portion_weight_g: float | None = Field(default=None, gt=0, le=100000)
    allergen_info_status: Literal["complete", "partial", "unknown"] = "unknown"
    allergens: list[AllergenDeclaration] = Field(default_factory=list, max_length=100)
    nutrition: NutritionFacts = Field(default_factory=NutritionFacts)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)


class MenuItem(Model):
    id: UUID
    dish_id: UUID
    restaurant_id: UUID
    menu_version_id: UUID
    name: str
    description: str | None = None
    ingredients: list[str]
    ingredients_complete: bool = False
    tags: list[str]
    price_minor: int
    currency: str
    portion_weight_g: float | None = None
    allergen_info_status: Literal["complete", "partial", "unknown"]
    allergens: list[AllergenDeclaration]
    nutrition: NutritionFacts
    evidence_ids: list[UUID]
    is_demo: bool = False


class MenuVersion(Model):
    id: UUID
    restaurant_id: UUID
    import_id: UUID
    version: int
    status: Literal["published", "archived"]
    published_at: datetime
    currency: str


class MenuResponse(Model):
    menu: MenuVersion
    items: list[MenuItem]


class MenuImport(Model):
    id: UUID
    restaurant_id: UUID
    source_kind: Literal["html", "pdf", "image"]
    filename: str
    status: Literal["queued", "processing", "needs_review", "published", "failed"]
    progress_percent: int
    created_at: datetime
    updated_at: datetime
    error: dict | None = None
    published_menu_version_id: UUID | None = None


class MenuDraft(Model):
    import_id: UUID
    revision: int
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    items: list[DraftItem]
    validation_warnings: list[str]


class MenuDraftReplace(Model):
    expected_revision: int = Field(ge=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    items: list[DraftItem] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_keys(self):
        if len({x.draft_key for x in self.items}) != len(self.items):
            raise ValueError("Draft keys must be unique")
        return self


class MenuPublishRequest(Model):
    expected_revision: int = Field(ge=1)


class Constraints(Model):
    restaurant_id: UUID | None = None
    near: Near | None = None
    budget_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")
    max_calories_kcal: float | None = Field(default=None, ge=0)
    min_protein_g: float | None = Field(default=None, ge=0)
    excluded_allergen_codes: list[str] = Field(default_factory=list, max_length=100)
    excluded_ingredients: list[str] = Field(default_factory=list, max_length=100)
    spice_preference: Literal["none", "mild", "any"] = "any"


class SearchRequest(Model):
    query: str = Field(min_length=1, max_length=2000)
    constraints: Constraints = Field(default_factory=Constraints)
    limit: int = Field(default=10, ge=1, le=50)


class SearchHit(Model):
    item: MenuItem
    restaurant: Restaurant
    relevance_score: float = Field(ge=0, le=1)
    cosine_similarity: float = Field(ge=-1, le=1)
    ranking_score: float = Field(ge=0, le=2)
    matched_query_terms: list[str]
    ranking_method: str = "cosine_plus_explicit_name_tag_bonus"
    evidence_ids: list[UUID]
    warnings: list[str]


class SearchResponse(Model):
    items: list[SearchHit]
    warnings: list[str]
    embedding_model: str
