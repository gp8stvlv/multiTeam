"""Explicit fake-model tests of real LangGraph routing and deterministic validation."""
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from services.recommendations.graph import PipelineError, RecommendationGraph, tighten
from services.recommendations.schemas import RecommendationCreate, RecommendationResult
from services.users.nutrition import aggregate_nutrition, calculate_price
from services.users.schemas import NutritionFacts


ITEM_ID = "20000000-0000-4000-8000-000000000001"
DISH_ID = "00000000-0000-4000-8000-000000000003"
RESTAURANT_ID = "00000000-0000-4000-8000-000000000001"
VERSION_ID = "00000000-0000-4000-8000-000000000002"
EVIDENCE_ID = "00000000-0000-4000-8000-000000000009"
RESTAURANT = {
    "id": RESTAURANT_ID, "name": "Demo restaurant", "address": "Test address",
    "location": {"latitude": 55.75, "longitude": 37.61}, "cuisines": ["georgian"],
    "timezone": "Europe/Moscow", "active": True, "active_menu_version_id": VERSION_ID,
    "updated_at": "2026-10-11T10:30:00Z", "is_demo": True,
}
ITEM = {
    "id": ITEM_ID, "dish_id": DISH_ID, "restaurant_id": RESTAURANT_ID,
    "menu_version_id": VERSION_ID, "name": "Demo khinkali", "ingredients": ["wheat", "beef"],
    "ingredients_complete": True, "tags": ["mild"], "price_minor": 75000, "currency": "RUB",
    "portion_weight_g": 360, "allergen_info_status": "partial",
    "allergens": [{"code": "gluten", "status": "contains", "evidence_id": EVIDENCE_ID}],
    "nutrition": {"calories_kcal": 620, "protein_g": 32, "fat_g": 22, "carbs_g": 73.5,
                  "basis": "per_portion", "source": "restaurant_declared", "method": "Test fixture",
                  "evidence_ids": [EVIDENCE_ID]}, "evidence_ids": [EVIDENCE_ID], "is_demo": True,
}
PROFILE = {
    "user_id": "00000000-0000-4000-8000-000000000010", "timezone": "Europe/Moscow",
    "favorite_cuisines": ["georgian"], "disliked_ingredients": [], "spice_preference": "mild",
    "allergies": [], "default_budget_minor": 90000, "daily_calories_target": 2200,
    "currency": "RUB", "proactive_enabled": False,
}


def message(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": str(uuid4()), "type": "tool_call"}],
                     usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


class ScriptedModel:
    """Deterministic bind_tools/ainvoke test model."""
    def __init__(self, selections=None, decisions=None, empty_plan=False):
        self.selections = selections if selections is not None else [{"menu_item_id": ITEM_ID, "portions": 1}]
        self.empty_plan = empty_plan
        self.decisions = list(decisions or [{"action": action} for action in ("search", "nutrition", "planner", "finish")])
        self.calls = []

    def bind_tools(self, tools, **kwargs):
        parent = self
        name = getattr(tools[0], "name", None) or tools[0].__name__

        class Bound:
            async def ainvoke(self, messages):
                parent.calls.append((name, messages))
                if name == "CoordinatorDecision":
                    return message(name, parent.decisions.pop(0) if parent.decisions else {"action": "finish"})
                if name == "MealPlan":
                    return message(name, {"options": [] if parent.empty_plan else [{"items": parent.selections, "reason_codes": ["query_match", "budget_fit"]}]})
                if isinstance(messages[-1], ToolMessage):
                    return AIMessage(content="Inspection complete.")
                if name == "search_menu":
                    return message(name, {"query": "Хочу хинкали"})
                if name == "calculate_meal":
                    return message(name, {"menu_item_ids": [ITEM_ID]})
                raise AssertionError(f"Unexpected fake-model binding: {name}")

        return Bound()


class World:
    def __init__(self):
        self.item = deepcopy(ITEM)
        self.restaurant = deepcopy(RESTAURANT)
        self.profile = deepcopy(PROFILE)
        self.evidence = {"id": EVIDENCE_ID, "restaurant_id": RESTAURANT_ID, "menu_item_id": ITEM_ID}
        self.calls = []
        self.search_status = 200
        self.empty_search = False

    def respond(self, request):
        self.calls.append(request)
        path = request.url.path
        if path.endswith("/me/profile"):
            return httpx.Response(200, json=self.profile)
        if path.endswith("/me/daily-summary"):
            return httpx.Response(200, json={"remaining": {"calories_kcal": 2200}, "entry_count": 0})
        if path.endswith("/me/feedback"):
            return httpx.Response(200, json={"items": [{"dish_id": DISH_ID, "rating": "like"}]})
        if path.endswith("/search"):
            return httpx.Response(self.search_status, json={"items": [] if self.empty_search else [{
                "item": self.item, "restaurant": self.restaurant, "relevance_score": 0.9,
                "evidence_ids": [EVIDENCE_ID], "warnings": [],
            }], "warnings": [], "embedding_model": "explicit-test-encoder"})
        if path.endswith("/menu-items/" + ITEM_ID):
            return httpx.Response(200, json=self.item)
        if path.endswith("/restaurants/" + RESTAURANT_ID):
            return httpx.Response(200, json=self.restaurant)
        if path.endswith("/evidence/" + EVIDENCE_ID):
            return httpx.Response(200, json=self.evidence)
        if path.endswith("/nutrition/calculate"):
            import json
            portions = [Decimal(str(item["portions"])) for item in json.loads(request.content)["items"]]
            nutrition = NutritionFacts.model_validate(self.item["nutrition"])
            totals = aggregate_nutrition((nutrition, portion) for portion in portions)
            return httpx.Response(200, json={"items": json.loads(request.content)["items"],
                "price_minor": calculate_price((self.item["price_minor"], portion) for portion in portions),
                "currency": self.item["currency"], "nutrition": totals.model_dump(mode="json"), "warnings": []})
        raise AssertionError(f"Unexpected HTTP request: {request.method} {request.url}")


async def run_world(world, request=None, model=None):
    traces = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.respond)) as client:
        graph = RecommendationGraph("explicit-test-user-token", traces.append, model=model or ScriptedModel(), client=client)
        result = await graph.run(request or RecommendationCreate(mode="dish_search", query="Хочу хинкали", max_options=3))
    return result, traces


@pytest.mark.asyncio
async def test_real_langgraph_specialists_tools_final_calculation_and_trace():
    world = World()
    result, traces = await run_world(world)
    checked = RecommendationResult.model_validate(result)
    assert checked.outcome == "ready"
    option = checked.options[0]
    assert option.total_price_minor == 75000
    assert option.nutrition.carbs_g == 73.5
    assert option.items[0].name == ITEM["name"]
    assert [str(value) for value in option.evidence_ids] == [EVIDENCE_ID]
    assert option.warnings  # Demo data and partial allergen declarations remain visible.
    assert [trace["agent"] for trace in traces] == ["coordinator", "search", "coordinator", "nutrition", "coordinator", "planner", "coordinator", "validator"]
    assert all(trace["status"] == "completed" for trace in traces)
    assert {name for trace in traces for name in trace["tool_names"]} == {"search_menu", "calculate_meal"}
    assert all(call.headers["Authorization"] == "Bearer explicit-test-user-token" for call in world.calls)


@pytest.mark.asyncio
async def test_allergy_from_profile_cannot_be_relaxed_by_coordinator():
    world = World()
    world.profile["allergies"] = [{"code": "peanut", "note": "Must exclude"}]
    model = ScriptedModel(decisions=[
        {"action": "search", "additional_constraints": {"excluded_allergen_codes": []}},
        {"action": "nutrition"}, {"action": "planner"}, {"action": "finish"},
    ])
    result, traces = await run_world(world, model=model)
    assert result["outcome"] == "no_matches"
    assert result["options"] == []
    import json
    search = next(call for call in world.calls if call.url.path.endswith("/search"))
    assert json.loads(search.content)["constraints"]["excluded_allergen_codes"] == ["peanut"]
    assert sum(trace["agent"] == "validator" for trace in traces) >= 2  # Rejection causes real re-planning.


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["budget", "unknown_calories", "stale_menu", "wrong_evidence_owner"])
async def test_final_validation_rejects_unsupported_or_outdated_choices(failure):
    world = World()
    constraints = {}
    if failure == "budget":
        constraints["budget_minor"] = 50000
    elif failure == "unknown_calories":
        constraints["max_calories_kcal"] = 700
        world.item["nutrition"]["calories_kcal"] = None
    elif failure == "stale_menu":
        world.restaurant["active_menu_version_id"] = str(uuid4())
    elif failure == "wrong_evidence_owner":
        world.evidence["restaurant_id"] = str(uuid4())
    result, _ = await run_world(world, RecommendationCreate(mode="dish_search", query="Хинкали", constraints=constraints))
    assert result["outcome"] == "no_matches"
    assert result["options"] == []


@pytest.mark.asyncio
async def test_allergen_exclusion_can_pass_only_with_explicit_source_declaration():
    world = World()
    world.profile["allergies"] = [{"code": "peanut"}]
    world.item["allergens"].append({"code": "peanut", "status": "declared_absent", "evidence_id": EVIDENCE_ID})
    result, _ = await run_world(world)
    assert result["outcome"] == "ready"


@pytest.mark.asyncio
async def test_empty_successful_search_is_no_matches():
    world = World()
    world.empty_search = True
    result, _ = await run_world(world)
    assert result["outcome"] == "no_matches"


@pytest.mark.asyncio
async def test_empty_plan_after_finish_is_not_ready():
    result, _ = await run_world(World(), model=ScriptedModel(empty_plan=True))
    assert result["outcome"] == "no_matches"
    assert result["options"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selections", [
    [{"menu_item_id": "ffffffff-0000-4000-8000-000000000000", "portions": 1}],
    [{"menu_item_id": ITEM_ID, "portions": 1}, {"menu_item_id": ITEM_ID, "portions": 1}],
])
async def test_invalid_plan_never_becomes_ready(selections):
    result, _ = await run_world(World(), model=ScriptedModel(selections=selections))
    assert result["outcome"] == "no_matches"
    assert result["options"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("minimum,outcome", [(50, "ready"), (70, "no_matches")])
async def test_meal_protein_minimum_filters_total_meal_not_each_dish(minimum, outcome):
    world = World()
    request = RecommendationCreate(mode="meal_plan", query="Обед с белком",
        constraints={"budget_minor": 200000, "min_protein_g": minimum})
    result, _ = await run_world(world, request,
        model=ScriptedModel(selections=[{"menu_item_id": ITEM_ID, "portions": 2}]))
    import json
    search_requests = [call for call in world.calls if call.url.path.endswith("/search")]
    assert search_requests
    assert all(json.loads(call.content)["constraints"]["min_protein_g"] is None for call in search_requests)
    assert result["outcome"] == outcome
    if outcome == "ready":
        assert result["options"][0]["nutrition"]["protein_g"] == 64
        assert result["options"][0]["total_price_minor"] == 150000
    else:
        assert result["options"] == []


@pytest.mark.asyncio
async def test_dish_search_retains_individual_protein_minimum():
    world = World()
    result, _ = await run_world(world, RecommendationCreate(mode="dish_search", query="Хинкали с белком",
        constraints={"min_protein_g": 50}))
    import json
    search = next(call for call in world.calls if call.url.path.endswith("/search"))
    assert json.loads(search.content)["constraints"]["min_protein_g"] == 50
    assert result["outcome"] == "no_matches"


@pytest.mark.asyncio
async def test_dependency_failure_must_not_be_reported_as_no_matches():
    world = World()
    world.search_status = 503
    with pytest.raises(PipelineError) as failure:
        await run_world(world)
    assert failure.value.code == "dependency_unavailable"


@pytest.mark.asyncio
async def test_tool_call_overflow_never_sends_unanswered_calls_to_provider():
    class StrictProtocolModel:
        def __init__(self):
            self.round = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.round += 1
            if self.round == 1:
                return AIMessage(content="", tool_calls=[
                    {"name": "inspect", "args": {"id": index}, "id": f"call-{index}", "type": "tool_call"}
                    for index in range(6)
                ])
            issued = {call["id"] for message in messages if isinstance(message, AIMessage) for call in message.tool_calls}
            answered = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
            assert issued == answered, "Provider would reject unmatched tool_call_ids"
            return AIMessage(content="Done")

    @tool
    async def inspect(id: int) -> dict:
        """Inspect one integer fixture."""
        return {"id": id}

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        graph = RecommendationGraph("test-token", lambda _: None, model=StrictProtocolModel(), client=client)
        graph.metrics = {"input_tokens": 0, "output_tokens": 0, "tool_names": []}
        await graph.agent_tools("Use the inspection tool", [inspect], "inspect")


def test_tightening_preserves_stricter_mandatory_constraints():
    result = tighten(
        {"budget_minor": 90000, "max_calories_kcal": 650, "min_protein_g": 30,
         "excluded_allergen_codes": ["peanut"], "excluded_ingredients": ["cilantro"], "spice_preference": "none"},
        {"budget_minor": 200000, "max_calories_kcal": 2000, "min_protein_g": 0,
         "excluded_allergen_codes": [], "excluded_ingredients": [], "spice_preference": "any"},
    )
    assert result["budget_minor"] == 90000 and result["max_calories_kcal"] == 650
    assert result["min_protein_g"] == 30
    assert result["excluded_allergen_codes"] == ["peanut"]
    assert result["excluded_ingredients"] == ["cilantro"]
    assert result["spice_preference"] == "none"
