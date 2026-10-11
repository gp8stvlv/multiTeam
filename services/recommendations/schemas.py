from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from services.catalog.schemas import Constraints, Restaurant, NutritionFacts


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecommendationCreate(Model):
    mode: Literal["dish_search", "meal_plan"]
    query: str = Field(min_length=1, max_length=2000)
    constraints: Constraints = Field(default_factory=Constraints)
    max_options: int = Field(default=3, ge=1, le=5)
    context_request_id: UUID | None = None


class NutritionTotals(Model):
    calories_kcal: float | None
    protein_g: float | None
    fat_g: float | None
    carbs_g: float | None
    known_subtotals: dict[str, float]
    complete_fields: list[str]
    sources: list[str]


class OptionItem(Model):
    menu_item_id: UUID
    dish_id: UUID
    name: str
    portions: int = Field(ge=1, le=20)
    unit_price_minor: int
    nutrition_per_portion: NutritionFacts


class RecommendationOption(Model):
    id: UUID
    rank: int
    restaurant: Restaurant
    items: list[OptionItem]
    total_price_minor: int
    currency: str
    nutrition: NutritionTotals
    reasons: list[str]
    warnings: list[str]
    evidence_ids: list[UUID]


class Clarification(Model):
    field: str
    question: str
    suggested_values: list[str] = Field(default_factory=list)


class RecommendationResult(Model):
    id: UUID
    outcome: Literal["ready", "needs_clarification", "no_matches"]
    options: list[RecommendationOption]
    clarifications: list[Clarification]
    warnings: list[str]
    created_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        if self.outcome == "ready" and not self.options:
            raise ValueError("Ready results require options")
        if self.outcome != "ready" and self.options:
            raise ValueError("Non-ready results cannot contain options")
        if self.outcome == "needs_clarification" and not self.clarifications:
            raise ValueError("Clarification required")
        return self


class Error(Model):
    code: str
    message: str
    request_id: str
    details: dict = Field(default_factory=dict)


class RecommendationRun(Model):
    id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    trigger: Literal["manual", "proactive"]
    created_at: datetime
    updated_at: datetime
    result: RecommendationResult | None = None
    error: Error | None = None


class AgentRun(Model):
    id: UUID
    agent: Literal["coordinator", "search", "nutrition", "planner", "validator"]
    status: Literal["running", "completed", "failed", "skipped"]
    started_at: datetime
    finished_at: datetime | None = None
    tool_names: list[str]
    evidence_ids: list[UUID]
    summary: str
    input_tokens: int = 0
    output_tokens: int = 0


class TraceResponse(Model):
    recommendation_id: UUID
    runs: list[AgentRun]


class FeedItem(Model):
    id: UUID
    recommendation_id: UUID
    title: str
    status: Literal["unread", "read", "dismissed"]
    created_at: datetime
    expires_at: datetime


class FeedPage(Model):
    items: list[FeedItem]
    total: int
    limit: int
    offset: int


class FeedPatch(Model):
    status: Literal["read", "dismissed"]


class Selection(Model):
    menu_item_id: str
    portions: int = Field(default=1, ge=1, le=20)


class PlannedOption(Model):
    items: list[Selection] = Field(min_length=1, max_length=6)
    reason_codes: list[Literal["query_match", "preferred_cuisine", "liked_dish", "nearby", "well_rated", "nutrition_fit", "budget_fit"]] = Field(default_factory=list)


class MealPlan(Model):
    options: list[PlannedOption] = Field(max_length=5)


class CoordinatorDecision(Model):
    action: Literal["search", "nutrition", "planner", "finish", "clarify"]
    search_query: str | None = Field(default=None, max_length=2000)
    additional_constraints: Constraints | None = None
    clarifications: list[Clarification] = Field(default_factory=list, max_length=3)
