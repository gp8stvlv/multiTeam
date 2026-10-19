"""Explicit fake-model tests of real LangGraph routing and deterministic validation."""
from copy import deepcopy
from decimal import Decimal
import json
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from services.recommendations.graph import LLM_CANDIDATE_CHARS, PipelineError, RecommendationGraph, llm_candidates, llm_reviews, tighten
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
                    assert "backend_retrieval" in messages[-1].content
                    return AIMessage(content="DONE")
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
@pytest.mark.parametrize("empty_search", [False, True])
async def test_repeated_search_decisions_are_bounded_and_candidates_reach_planning(empty_search):
    world = World()
    world.empty_search = empty_search
    model = ScriptedModel(decisions=[{"action": "search"}] * 10)
    result, traces = await run_world(world, model=model)
    assert result["outcome"] == ("no_matches" if empty_search else "ready")
    assert sum(trace["agent"] == "search" for trace in traces) == (2 if empty_search else 1)
    assert sum(name == "CoordinatorDecision" for name, _ in model.calls) == (2 if empty_search else 1)
    assert sum(trace["agent"] == "nutrition" for trace in traces) == (0 if empty_search else 1)
    assert sum(trace["agent"] == "planner" for trace in traces) == (0 if empty_search else 1)
    assert any("action=" + ("finish" if empty_search else "nutrition") in trace["summary"]
               for trace in traces if trace["agent"] == "coordinator")
    if not empty_search:
        assert result["options"][0]["total_price_minor"] == ITEM["price_minor"]
        assert result["options"][0]["evidence_ids"] == [EVIDENCE_ID]
        assert len(model.calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("reference,outcome", [("m1", "ready"), ("m999", "no_matches"), ("r1", "no_matches")])
async def test_model_references_resolve_before_authoritative_checks_and_unknown_refs_fail(reference, outcome):
    world = World()
    model = ScriptedModel(selections=[{"menu_item_id": reference, "portions": 1}])
    result, traces = await run_world(world, model=model)
    assert result["outcome"] == outcome
    assert not any(request.url.path.endswith("/" + reference) for request in world.calls)
    if outcome == "ready":
        assert result["options"][0]["items"][0]["menu_item_id"] == ITEM_ID
        assert result["options"][0]["evidence_ids"] == [EVIDENCE_ID]
    assert '"m1"' not in json.dumps({"result": result, "traces": traces})


@pytest.mark.asyncio
async def test_adaptive_search_can_read_reviews_using_restaurant_references():
    seen = []

    class ReviewModel:
        calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                assert isinstance(messages[-1], HumanMessage)
                payload = json.loads(messages[-1].content.split("\n")[-1])["result"]
                assert payload["restaurants"][0]["id"] == "r1"
                return message("restaurant_reviews", {"restaurant_id": "r1"})
            assert self.calls == 2
            payload = json.loads(messages[-1].content)
            assert payload["items"][0]["id"] == "v1"
            assert payload["items"][0]["evidence_id"] == "e1"
            return AIMessage(content="Complete")

    world = World()

    def respond(request):
        if request.url.path.endswith("/reviews"):
            seen.append(request.url.path)
            return httpx.Response(200, json={"items": [{"id": str(uuid4()), "restaurant_id": RESTAURANT_ID,
                "rating": 4, "text": "Good", "source": "fixture", "evidence_id": EVIDENCE_ID}]})
        return world.respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        graph = RecommendationGraph("test-token", lambda _: None, model=ReviewModel(), client=client)
        graph.metrics = {"input_tokens": 0, "output_tokens": 0, "tool_names": []}
        state = {"candidates": {}, "constraints": {}, "request": {"mode": "meal_plan"}, "search_query": "хинкали", "search_count": 0}
        updated = await graph.search(state)
    assert seen == [f"/api/v1/restaurants/{RESTAURANT_ID}/reviews"]
    assert ITEM_ID in updated["candidates"]


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
async def test_nutrition_dependency_failure_remains_an_error_with_early_finish():
    class UnavailableNutrition(World):
        def respond(self, request):
            if request.url.path.endswith("/nutrition/calculate"):
                return httpx.Response(503)
            return super().respond(request)

    with pytest.raises(PipelineError) as failure:
        await run_world(UnavailableNutrition())
    assert failure.value.code == "dependency_unavailable"


@pytest.mark.asyncio
async def test_early_tool_finish_recovers_invalid_arguments_and_executes_the_full_batch():
    executed = []

    class RecoveringModel:
        calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return message("inspect", {"id": -1})
            assert self.calls == 2, "Successful calculations must not need a final LLM acknowledgement"
            assert isinstance(messages[-1], ToolMessage) and "invalid_tool_input" in messages[-1].content
            return AIMessage(content="", tool_calls=[
                {"name": "inspect", "args": {"id": value}, "id": str(uuid4()), "type": "tool_call"}
                for value in (1, 2)
            ])

    @tool
    async def inspect(id: int) -> dict:
        """Inspect a positive fixture ID."""
        if id < 0:
            raise ValueError("Unknown ID")
        executed.append(id)
        return {"id": id}

    async with httpx.AsyncClient() as client:
        model = RecoveringModel()
        graph = RecommendationGraph("test-token", lambda _: None, model=model, client=client)
        graph.metrics = {"input_tokens": 0, "output_tokens": 0, "tool_names": []}
        await graph.agent_tools("Inspect fixtures", [inspect], "inspect", finish_on_success=True)
    assert executed == [1, 2] and model.calls == 2


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


@pytest.mark.asyncio
async def test_refined_search_candidates_take_priority_in_the_bounded_model_view():
    from services.recommendations.graph import llm_candidates

    batches = []
    for _ in range(2):
        batch = []
        for _ in range(20):
            item = deepcopy(ITEM)
            item["id"] = str(uuid4())
            batch.append({"item": item, "restaurant": deepcopy(RESTAURANT), "evidence_ids": [EVIDENCE_ID]})
        batches.append(batch)
    searches = []

    def respond(request):
        searches.append(request)
        return httpx.Response(200, json={"items": batches[len(searches) - 1]})

    class RefiningModel:
        calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return message("search_menu", {"query": "refined"})
            return AIMessage(content="Done")

    state = {"candidates": {}, "constraints": {}, "request": {"mode": "meal_plan"},
             "search_query": "initial", "search_count": 0}
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        graph = RecommendationGraph("test-token", lambda _: None, model=RefiningModel(), client=client)
        graph.metrics = {"input_tokens": 0, "output_tokens": 0, "tool_names": []}
        update = await graph.search(state)
    assert len(update["candidates"]) == 40 and state["candidates"] == {}
    assert [json.loads(request.content)["query"] for request in searches] == ["initial", "refined"]
    projected = llm_candidates(update["candidates"])
    visible = {item["menu_item_id"] for item in projected["items"]}
    assert len(visible) >= 5
    assert visible.issubset({hit["item"]["id"] for hit in batches[1]})
    assert visible.isdisjoint({hit["item"]["id"] for hit in batches[0]})


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


def long_menu_item():
    item = deepcopy(ITEM)
    item["description"] = "Описание блюда. " * 240 + "FULL_DESCRIPTION_TAIL"
    item["ingredients"] = [f"ingredient-{index}-" + "x" * 180 for index in range(99)] + ["cilantro"]
    item["tags"] += ["tag-" + str(index) + "x" * 150 for index in range(49)]
    item["nutrition"]["source"] = "estimated"
    item["nutrition"]["method"] = "FULL_METHOD_ONLY_" + "m" * 1900
    return item


def test_compact_candidates_bound_model_payload_without_mutating_source_records():
    candidates = {}
    for _ in range(40):
        item = long_menu_item()
        item["id"] = str(uuid4())
        candidates[item["id"]] = {"item": item, "restaurant": deepcopy(RESTAURANT),
                                  "evidence_ids": [EVIDENCE_ID], "warnings": []}
    original = deepcopy(candidates)
    projected = llm_candidates(candidates)
    encoded = json.dumps(projected, ensure_ascii=False)
    assert len(encoded) <= LLM_CANDIDATE_CHARS
    assert len(encoded) < len(json.dumps(candidates, ensure_ascii=False)) / 10
    assert 5 <= len(projected["items"]) <= 20
    assert projected["candidate_count"] == 40
    assert projected["omitted_count"] == 40 - len(projected["items"])
    assert len(projected["restaurants"]) == 1  # Restaurant data is shared across dishes.
    assert "FULL_DESCRIPTION_TAIL" not in encoded and "FULL_METHOD_ONLY_" not in encoded
    for item in projected["items"]:
        assert item["menu_item_id"] in candidates
        assert item["evidence_ids"] == [EVIDENCE_ID]
        assert item["evidence_count"] == 1
        assert item["nutrition_per_portion"]["source"] == "estimated"
        assert "ingredients" in item["truncated_fields"]
    assert candidates == original


def test_compact_reviews_bound_untrusted_text_and_preserve_source_identity():
    review = {"id": str(uuid4()), "restaurant_id": RESTAURANT_ID, "rating": 4.5,
              "text": "review " * 10000, "source": "restaurant", "evidence_id": EVIDENCE_ID, "is_demo": False,
              "source_url": "https://example.test/" + "x" * 5000}
    original = {"items": [deepcopy(review) for _ in range(6)], "total": 6}
    compact = llm_reviews(original)
    assert len(json.dumps(compact)) < 3500
    assert len(compact["items"]) == 5 and compact["total"] == 6
    assert len(compact["items"][0]["text"]) == 280
    assert compact["items"][0]["evidence_id"] == EVIDENCE_ID
    assert compact["items"][0]["is_demo"] is False
    assert original["items"][0] == review


@pytest.mark.asyncio
@pytest.mark.parametrize("exclude_hidden_ingredient", [False, True])
async def test_compact_tool_payloads_keep_full_records_and_final_checks(exclude_hidden_ingredient):
    world = World()
    world.item = long_menu_item()
    if exclude_hidden_ingredient:
        world.profile["disliked_ingredients"] = ["cilantro"]
    model = ScriptedModel()
    snapshots = []

    class InspectingGraph(RecommendationGraph):
        async def validate(self, state):
            snapshots.append(deepcopy({key: state[key] for key in ("candidates", "inspections")}))
            return await super().validate(state)

    async with httpx.AsyncClient(transport=httpx.MockTransport(world.respond)) as client:
        graph = InspectingGraph("test-token", lambda _: None, model=model, client=client)
        result = await graph.run(RecommendationCreate(mode="dish_search", query="Хочу хинкали"))
    assert result["outcome"] == ("no_matches" if exclude_hidden_ingredient else "ready")
    assert snapshots[0]["candidates"][ITEM_ID]["item"] == world.item
    assert snapshots[0]["inspections"][0]["nutrition"]["calories_kcal"] == 620
    model_contexts = "\n".join(str(message.content) for _, messages in model.calls for message in messages)
    assert "FULL_DESCRIPTION_TAIL" not in model_contexts and "FULL_METHOD_ONLY_" not in model_contexts
    search_replies = [json.loads(message.content.split("\n")[-1])["result"]
                      for name, messages in model.calls if name == "search_menu"
                      for message in messages if isinstance(message, HumanMessage)
                      and "backend_retrieval" in message.content]
    assert search_replies
    assert graph.references.resolve_menu(search_replies[0]["items"][0]["menu_item_id"], {ITEM_ID}) == ITEM_ID
    assert search_replies[0]["items"][0]["evidence_ids"] == ["e1"]
    assert len(json.dumps(search_replies[0], ensure_ascii=False)) < 6000
    assert any(request.url.path.endswith("/menu-items/" + ITEM_ID) for request in world.calls)
    if not exclude_hidden_ingredient:
        checked = RecommendationResult.model_validate(result)
        assert checked.options[0].total_price_minor == ITEM["price_minor"]
        assert checked.options[0].items[0].nutrition_per_portion.method == world.item["nutrition"]["method"]
        assert [str(value) for value in checked.options[0].evidence_ids] == [EVIDENCE_ID]
