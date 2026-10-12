from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from email_validator import EmailNotValidError, validate_email

from pydantic import (
    BaseModel, ConfigDict, EmailStr, Field, PlainSerializer, field_validator, model_validator,
)


Number = Annotated[Decimal, PlainSerializer(lambda value: float(value), return_type=float, when_used="json")]
NonNegative = Annotated[Number, Field(ge=0, max_digits=20, decimal_places=8)]
Portions = Annotated[Number, Field(gt=0, le=20, max_digits=12, decimal_places=8)]
Source = Literal["restaurant_declared", "estimated", "user_entered", "unknown"]
Nutrient = Literal["calories_kcal", "protein_g", "fat_g", "carbs_g"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class AccountEmail(EmailStr):
    """Validate email syntax while allowing .test addresses."""
    @classmethod
    def _validate(cls, value: str) -> str:
        try:
            return validate_email(value, check_deliverability=False, test_environment=True).normalized
        except EmailNotValidError as exc:
            raise ValueError(str(exc)) from exc


class RegisterRequest(Model):
    email: AccountEmail
    password: str = Field(min_length=8, max_length=128, json_schema_extra={"writeOnly": True})


class LoginRequest(Model):
    email: AccountEmail
    password: str = Field(min_length=1, max_length=128, json_schema_extra={"writeOnly": True})


class User(Model):
    id: UUID
    email: AccountEmail
    role: Literal["user", "admin"]
    created_at: datetime


class AuthResponse(Model):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(ge=1)
    user: User


class Allergy(Model):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    note: str | None = Field(default=None, max_length=500)


class ProfileFields(Model):
    timezone: str = "UTC"
    favorite_cuisines: list[str] = Field(default_factory=list, max_length=30)
    disliked_ingredients: list[str] = Field(default_factory=list, max_length=100)
    spice_preference: Literal["none", "mild", "any"] = "any"
    allergies: list[Allergy] = Field(default_factory=list, max_length=30)
    daily_calories_target: int | None = Field(default=None, ge=1, le=100000)
    daily_protein_target_g: NonNegative | None = None
    daily_fat_target_g: NonNegative | None = None
    daily_carbs_target_g: NonNegative | None = None
    default_budget_minor: int | None = Field(default=None, ge=0, le=1000000000)
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")
    proactive_enabled: bool = False

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA identifier") from exc
        return value

    @field_validator("favorite_cuisines", "disliked_ingredients")
    @classmethod
    def unique_strings(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("Each preference must have 1 to 100 characters")
        if len(set(value.casefold().strip() for value in values)) != len(values):
            raise ValueError("Duplicate preferences are not allowed")
        return [value.strip() for value in values]

    @field_validator("allergies")
    @classmethod
    def unique_allergies(cls, values: list[Allergy]) -> list[Allergy]:
        if len({value.code for value in values}) != len(values):
            raise ValueError("Duplicate allergy codes are not allowed")
        return values


class Profile(ProfileFields):
    user_id: UUID
    updated_at: datetime


class ProfilePatch(Model):
    timezone: str | None = None
    favorite_cuisines: list[str] | None = None
    disliked_ingredients: list[str] | None = None
    spice_preference: Literal["none", "mild", "any"] | None = None
    allergies: list[Allergy] | None = None
    daily_calories_target: int | None = Field(default=None, ge=1, le=100000)
    daily_protein_target_g: NonNegative | None = None
    daily_fat_target_g: NonNegative | None = None
    daily_carbs_target_g: NonNegative | None = None
    default_budget_minor: int | None = Field(default=None, ge=0, le=1000000000)
    currency: str | None = None
    proactive_enabled: bool | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        fields = self.model_fields_set
        if not fields:
            raise ValueError("At least one field is required")
        optional = {"daily_calories_target", "daily_protein_target_g", "daily_fat_target_g",
                    "daily_carbs_target_g", "default_budget_minor"}
        if any(getattr(self, field) is None for field in fields - optional):
            raise ValueError("Only optional targets and budget can be cleared with null")
        # Validate merged defaults through the same validators as a complete profile.
        validated = ProfileFields.model_validate(self.model_dump(exclude_unset=True))
        for name in fields:
            setattr(self, name, getattr(validated, name))
        return self


class ManualNutrition(Model):
    calories_kcal: NonNegative | None
    protein_g: NonNegative | None
    fat_g: NonNegative | None
    carbs_g: NonNegative | None


class NutritionFacts(ManualNutrition):
    basis: Literal["per_portion"] = "per_portion"
    source: Source
    method: str | None = None
    evidence_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self):
        if self.source == "unknown" and any(
            getattr(self, name) is not None for name in ("calories_kcal", "protein_g", "fat_g", "carbs_g")
        ):
            raise ValueError("Unknown nutrition must have null values")
        if self.source == "estimated" and not self.method:
            raise ValueError("Estimated nutrition requires a method")
        return self


class AllergenDeclaration(Model):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    status: Literal["contains", "may_contain", "declared_absent"]
    evidence_id: UUID | None = None


class NutritionValues(Model):
    calories_kcal: Number | None
    protein_g: Number | None
    fat_g: Number | None
    carbs_g: Number | None


class KnownSubtotals(Model):
    calories_kcal: Annotated[Number, Field(ge=0)]
    protein_g: Annotated[Number, Field(ge=0)]
    fat_g: Annotated[Number, Field(ge=0)]
    carbs_g: Annotated[Number, Field(ge=0)]


class NutritionTotals(NutritionValues):
    known_subtotals: KnownSubtotals
    complete_fields: list[Nutrient]
    sources: list[Source]


class MealItemInput(Model):
    menu_item_id: UUID
    portions: Portions


class MealCalculateRequest(Model):
    items: list[MealItemInput] = Field(min_length=1, max_length=30)


class MealCalculation(Model):
    items: list[MealItemInput]
    price_minor: int = Field(ge=0)
    currency: str
    nutrition: NutritionTotals
    warnings: list[str]


class ConsumedFields(Model):
    consumed_at: datetime
    portions: Portions
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("consumed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("consumed_at must include a timezone offset")
        return value


class MenuDiaryEntryCreate(ConsumedFields):
    kind: Literal["menu_item"]
    menu_item_id: UUID


class ManualDiaryEntryCreate(ConsumedFields):
    kind: Literal["manual"]
    name: str = Field(min_length=1, max_length=200)
    nutrition_per_portion: ManualNutrition

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name cannot be whitespace")
        return value.strip()


DiaryEntryCreate = Annotated[
    MenuDiaryEntryCreate | ManualDiaryEntryCreate, Field(discriminator="kind")
]


class DiaryEntryPatch(Model):
    consumed_at: datetime | None = None
    portions: Portions | None = None
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        for name in ("consumed_at", "portions"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if self.consumed_at is not None:
            ConsumedFields.timezone_required(self.consumed_at)
        return self


class DiarySnapshot(Model):
    name: str
    menu_item_id: UUID | None
    dish_id: UUID | None
    restaurant_id: UUID | None
    menu_version_id: UUID | None
    ingredients: list[str]
    allergen_info_status: Literal["complete", "partial", "unknown"]
    allergens: list[AllergenDeclaration]
    portion_weight_g: NonNegative | None = None
    price_minor: int | None = Field(default=None, ge=0)
    currency: str | None = None
    nutrition_per_portion: NutritionFacts


class DiaryEntry(Model):
    id: UUID
    user_id: UUID
    kind: Literal["menu_item", "manual"]
    consumed_at: datetime
    portions: Portions
    note: str | None
    snapshot: DiarySnapshot
    consumed_nutrition: NutritionTotals
    created_at: datetime
    updated_at: datetime


class DiaryPage(Model):
    items: list[DiaryEntry]
    total: int
    limit: int
    offset: int


class DailySummary(Model):
    date: date
    timezone: str
    entry_count: int
    nutrition: NutritionTotals
    targets: NutritionValues
    remaining: NutritionValues
    warnings: list[str]


class FeedbackCreate(Model):
    dish_id: UUID
    recommendation_id: UUID | None = None
    rating: Literal["like", "dislike"]
    comment: str | None = Field(default=None, max_length=1000)


class Feedback(FeedbackCreate):
    id: UUID
    user_id: UUID
    updated_at: datetime


class FeedbackPage(Model):
    items: list[Feedback]


class UserContext(Model):
    user_id: UUID
    profile: Profile
    jwt: str


class UserContexts(Model):
    items: list[UserContext]
