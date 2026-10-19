"""Exact-cache checks using the real graph and isolated service/model fixtures."""
from datetime import datetime, timezone
import json

import httpx
import pytest

from services.recommendations.cache import CachedSelection, RecommendationCache, fingerprint
from services.recommendations.graph import PipelineError, RecommendationGraph
from services.recommendations.schemas import RecommendationCreate, RecommendationResult
from test_recommendations_graph import DISH_ID, ITEM_ID, PROFILE, ScriptedModel, World


USER_ID = PROFILE["user_id"]
INTERNAL_FIXTURE = "cache-tests-internal-token"


class RecordingCache(RecommendationCache):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.written_keys = []

    def put(self, key, selection):
        self.written_keys.append(key)
        super().put(key, selection)


class CacheWorld(World):
    def __init__(self):
        super().__init__()
        self.revision = "a" * 64
        self.revision_status = 200
        self.revision_payload = None
        self.summary = {"remaining": {"calories_kcal": 2200}, "entry_count": 0}
        self.feedback = {"items": [{"dish_id": DISH_ID, "rating": "like"}]}
        self.item_statuses = []
        self.before_validation = None

    def respond(self, request):
        path = request.url.path
        if path.endswith("/internal/recommendation-revision"):
            self.calls.append(request)
            assert request.headers["X-Internal-Token"] == INTERNAL_FIXTURE
            assert "Authorization" not in request.headers
            payload = self.revision_payload if self.revision_payload is not None else {"revision": self.revision}
            return httpx.Response(self.revision_status, json=payload)
        if path.endswith("/me/daily-summary"):
            self.calls.append(request)
            return httpx.Response(200, json=self.summary)
        if path.endswith("/me/feedback"):
            self.calls.append(request)
            return httpx.Response(200, json=self.feedback)
        if path.endswith("/menu-items/" + ITEM_ID):
            if self.before_validation is not None:
                callback, self.before_validation = self.before_validation, None
                callback(self)
            if self.item_statuses:
                status = self.item_statuses.pop(0)
                if status != 200:
                    self.calls.append(request)
                    return httpx.Response(status)
        return super().respond(request)


@pytest.fixture(autouse=True)
def explicit_cache_environment(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", INTERNAL_FIXTURE)
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "explicit-cache-fixture")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")
    monkeypatch.setenv("LLM_MAX_TOKENS", "6000")


async def run_cached(world, cache, *, request=None, model=None, user_id=USER_ID, previous=None):
    request = request or RecommendationCreate(mode="dish_search", query="Хочу хинкали", max_options=3)
    model = model if model is not None else ScriptedModel()
    traces = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.respond)) as client:
        graph = RecommendationGraph("cache-fixture-user-token", traces.append, model=model, client=client,
                                    cache=cache, user_id=user_id)
        result = await graph.run(request, previous)
    return RecommendationResult.model_validate(result), model, traces


def cache_hit(traces):
    return any("recommendation_cache=hit" in trace["summary"] for trace in traces)


def invalidated(traces):
    return any("recommendation_cache=invalidated" in trace["summary"] for trace in traces)


@pytest.mark.asyncio
async def test_exact_repeat_revalidates_live_services_without_llm_and_generates_new_ids():
    world, cache = CacheWorld(), RecordingCache()
    first, first_model, first_traces = await run_cached(world, cache)
    assert first.outcome == "ready" and len(cache.written_keys) == 1
    assert {name for name, _ in first_model.calls} == {"CoordinatorDecision", "search_menu", "calculate_meal", "MealPlan"}
    assert not cache_hit(first_traces)
    world.calls.clear()

    second, second_model, traces = await run_cached(world, cache)

    assert second.outcome == "ready" and second_model.calls == [] and cache_hit(traces)
    assert second.id != first.id and second.options[0].id != first.options[0].id
    assert second.options[0].total_price_minor == first.options[0].total_price_minor == 75000
    assert second.options[0].nutrition == first.options[0].nutrition
    assert second.options[0].evidence_ids == first.options[0].evidence_ids
    paths = [request.url.path for request in world.calls]
    assert any(path.endswith("/menu-items/" + ITEM_ID) for path in paths)
    assert any("/evidence/" in path for path in paths)
    assert any(path.endswith("/nutrition/calculate") for path in paths)
    assert not any(path.endswith("/search") for path in paths)
    assert {trace["agent"] for trace in traces} == {"coordinator", "validator"}
    assert all("llm_calls=0" in trace["summary"] for trace in traces)
    assert INTERNAL_FIXTURE not in json.dumps(traces)


@pytest.mark.asyncio
async def test_hit_recalculates_current_prices_and_macros_instead_of_reusing_result_numbers():
    world, cache = CacheWorld(), RecordingCache()
    first, _, _ = await run_cached(world, cache)
    # Keep the revision stale deliberately to prove seeds do not contain trusted totals.
    world.item["price_minor"] = 76000
    world.item["nutrition"]["calories_kcal"] = 640

    result, model, traces = await run_cached(world, cache)

    assert model.calls == [] and cache_hit(traces)
    assert first.options[0].total_price_minor == 75000
    assert result.options[0].total_price_minor == 76000
    assert result.options[0].nutrition.calories_kcal == 640
    assert result.options[0].items[0].nutrition_per_portion.calories_kcal == 640


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["profile", "summary", "feedback", "previous", "query", "mode", "constraints", "max_options", "catalog", "user"])
async def test_exact_cache_misses_after_request_context_catalog_or_owner_changes(change):
    world, cache = CacheWorld(), RecordingCache()
    request = RecommendationCreate(mode="dish_search", query="Хочу хинкали", max_options=3)
    await run_cached(world, cache, request=request)
    previous, user_id = None, USER_ID
    if change == "profile":
        world.profile["favorite_cuisines"] = ["italian"]
    elif change == "summary":
        world.summary["remaining"]["calories_kcal"] = 1500
    elif change == "feedback":
        world.feedback["items"][0]["rating"] = "dislike"
    elif change == "previous":
        previous = {"query": "Предыдущий запрос", "result": None}
    elif change == "query":
        request = request.model_copy(update={"query": "Хочу другие хинкали"})
    elif change == "mode":
        request = request.model_copy(update={"mode": "meal_plan"})
    elif change == "constraints":
        request = RecommendationCreate(mode="dish_search", query=request.query, constraints={"budget_minor": 80000})
    elif change == "max_options":
        request = request.model_copy(update={"max_options": 1})
    elif change == "catalog":
        world.revision = "b" * 64
    elif change == "user":
        user_id = "00000000-0000-4000-8000-000000000099"

    result, model, traces = await run_cached(world, cache, request=request, user_id=user_id, previous=previous)

    assert result.outcome == "ready" and model.calls and not cache_hit(traces)
    assert len(cache.written_keys) == 2 and cache.written_keys[0] != cache.written_keys[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("setting,value", [("LLM_BASE_URL", "https://another.example.test/v1"),
                                            ("LLM_MODEL", "different-model"),
                                            ("LLM_REASONING_EFFORT", "high"),
                                            ("LLM_MAX_TOKENS", "3000")])
async def test_provider_configuration_is_part_of_exact_key(monkeypatch, setting, value):
    world, cache = CacheWorld(), RecordingCache()
    await run_cached(world, cache)
    monkeypatch.setenv(setting, value)
    result, model, traces = await run_cached(world, cache)
    assert result.outcome == "ready" and model.calls and not cache_hit(traces)
    assert len(set(cache.written_keys)) == 2


@pytest.mark.asyncio
async def test_local_day_change_invalidates_even_when_summary_fixture_is_unchanged(monkeypatch):
    import services.recommendations.graph as graph_module

    class ControlledDateTime(datetime):
        instant = datetime(2026, 10, 19, 20, 59, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz) if tz is not None else cls.instant.replace(tzinfo=None)

    monkeypatch.setattr(graph_module, "datetime", ControlledDateTime)
    world, cache = CacheWorld(), RecordingCache()
    await run_cached(world, cache)
    ControlledDateTime.instant = datetime(2026, 10, 19, 21, 1, tzinfo=timezone.utc)
    result, model, traces = await run_cached(world, cache)
    assert result.outcome == "ready" and model.calls and not cache_hit(traces)
    assert len(set(cache.written_keys)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["budget", "ingredient"])
async def test_constraints_extracted_only_from_query_survive_cache_and_are_enforced(restriction):
    world, cache = CacheWorld(), RecordingCache()
    additions = {"budget_minor": 85000} if restriction == "budget" else {"excluded_ingredients": ["cilantro"]}
    request = RecommendationCreate(mode="dish_search", query="До 850 рублей и без кинзы")

    def model_with_extraction():
        return ScriptedModel(decisions=[{"action": "search", "additional_constraints": additions}])

    await run_cached(world, cache, request=request, model=model_with_extraction())
    seed = cache.get(cache.written_keys[0])
    assert all(seed.constraints[key] == value for key, value in additions.items())
    hit, model, traces = await run_cached(world, cache, request=request)
    assert hit.outcome == "ready" and model.calls == [] and cache_hit(traces)

    # Simulate a stale revision to exercise the Validator's independent defence.
    if restriction == "budget":
        world.item["price_minor"] = 87000
    else:
        world.item["ingredients"].append("cilantro")
    rejected, model, traces = await run_cached(world, cache, request=request, model=model_with_extraction())
    assert rejected.outcome == "no_matches" and not rejected.options
    assert model.calls and invalidated(traces) and not cache_hit(traces)
    assert cache.get(cache.written_keys[0]) is None


@pytest.mark.asyncio
async def test_partial_ready_cache_validation_discards_the_entire_cached_plan():
    world, cache = CacheWorld(), RecordingCache()
    await run_cached(world, cache)
    key = cache.written_keys[0]
    seed = cache.get(key)
    seed.plan["options"].append({"items": [{"menu_item_id": ITEM_ID, "portions": 2}]})
    cache.put(key, seed)

    result, model, traces = await run_cached(world, cache)

    assert result.outcome == "ready" and len(result.options) == 1
    assert result.options[0].total_price_minor == 75000
    assert model.calls and invalidated(traces) and not cache_hit(traces)


@pytest.mark.asyncio
async def test_cached_item_404_invalidates_and_runs_a_fresh_graph():
    world, cache = CacheWorld(), RecordingCache()
    await run_cached(world, cache)
    world.item_statuses = [404]
    result, model, traces = await run_cached(world, cache)
    assert result.outcome == "ready" and model.calls
    assert invalidated(traces) and not cache_hit(traces)


@pytest.mark.asyncio
async def test_cached_dependency_503_remains_an_error_instead_of_no_matches():
    world, cache = CacheWorld(), RecordingCache()
    await run_cached(world, cache)
    world.item_statuses = [503]
    model = ScriptedModel()
    with pytest.raises(PipelineError) as failure:
        await run_cached(world, cache, model=model)
    assert failure.value.code == "dependency_unavailable" and model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_revision", ["unavailable", "missing", "nonhex", "short", "wrong_shape"])
async def test_unavailable_or_malformed_catalog_revision_bypasses_existing_cache(bad_revision):
    world, cache = CacheWorld(), RecordingCache()
    await run_cached(world, cache)
    if bad_revision == "unavailable":
        world.revision_status = 503
    elif bad_revision == "missing":
        world.revision_payload = {}
    elif bad_revision == "nonhex":
        world.revision_payload = {"revision": "g" * 64}
    elif bad_revision == "short":
        world.revision_payload = {"revision": "a" * 63}
    elif bad_revision == "wrong_shape":
        world.revision_payload = []

    result, model, traces = await run_cached(world, cache)

    assert result.outcome == "ready" and model.calls and not cache_hit(traces)
    assert len(cache.written_keys) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["profile", "summary", "feedback", "catalog"])
async def test_changes_during_hit_validation_prevent_returning_the_hit(change):
    world, cache = CacheWorld(), RecordingCache()
    await run_cached(world, cache)

    def mutate(current):
        if change == "profile":
            current.profile["favorite_cuisines"] = ["japanese"]
        elif change == "summary":
            current.summary["remaining"]["calories_kcal"] = 1000
        elif change == "feedback":
            current.feedback["items"][0]["rating"] = "dislike"
        elif change == "catalog":
            current.revision = "c" * 64

    world.before_validation = mutate
    result, model, traces = await run_cached(world, cache)
    assert result.outcome == "ready" and model.calls
    assert invalidated(traces) and not cache_hit(traces)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["profile", "summary", "feedback", "catalog"])
async def test_changes_during_a_cold_run_prevent_storing_its_selection(change):
    world, cache = CacheWorld(), RecordingCache()

    def mutate(current):
        if change == "profile":
            current.profile["favorite_cuisines"] = ["japanese"]
        elif change == "summary":
            current.summary["remaining"]["calories_kcal"] = 1000
        elif change == "feedback":
            current.feedback["items"][0]["rating"] = "dislike"
        elif change == "catalog":
            current.revision = "d" * 64

    world.before_validation = mutate
    result, model, _ = await run_cached(world, cache)
    assert result.outcome == "ready" and model.calls
    assert cache.written_keys == []


@pytest.mark.asyncio
async def test_new_allergy_during_hit_validation_is_applied_to_fresh_graph():
    world, cache = CacheWorld(), RecordingCache()
    await run_cached(world, cache)
    world.before_validation = lambda current: current.profile.update(allergies=[{"code": "peanut"}])
    result, model, traces = await run_cached(world, cache)
    assert result.outcome == "no_matches" and not result.options
    assert model.calls and invalidated(traces) and not cache_hit(traces)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["no_matches", "needs_clarification", "error"])
async def test_nonready_results_and_errors_are_never_cached(outcome):
    world, cache = CacheWorld(), RecordingCache()
    model = ScriptedModel()
    if outcome == "no_matches":
        world.empty_search = True
    elif outcome == "needs_clarification":
        model = ScriptedModel(decisions=[{"action": "clarify", "clarifications": [
            {"field": "query", "question": "Что вы хотите заказать?"}]}])
    else:
        world.search_status = 503

    if outcome == "error":
        with pytest.raises(PipelineError):
            await run_cached(world, cache, model=model)
    else:
        result, _, _ = await run_cached(world, cache, model=model)
        assert result.outcome == outcome
    assert cache.written_keys == []


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["store", "hit"])
@pytest.mark.parametrize("failure", ["timeout", "dependency", "invalid_json"])
async def test_optional_cache_postcheck_failure_preserves_ready_result_or_falls_back(monkeypatch, phase, failure):
    world, cache = CacheWorld(), RecordingCache()
    if phase == "hit":
        await run_cached(world, cache)
    load_context = RecommendationGraph.load_user_context
    calls = 0

    async def failing_postcheck(self, previous=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            if failure == "timeout":
                raise TimeoutError("Explicit cache postcheck timeout fixture")
            if failure == "invalid_json":
                raise json.JSONDecodeError("Explicit malformed optional response", "", 0)
            raise PipelineError("dependency_unavailable", "Explicit optional context failure fixture")
        return await load_context(self, previous)

    monkeypatch.setattr(RecommendationGraph, "load_user_context", failing_postcheck)
    result, model, traces = await run_cached(world, cache)

    assert result.outcome == "ready" and result.options[0].total_price_minor == 75000
    assert model.calls and not cache_hit(traces)
    if phase == "store":
        assert calls == 2 and cache.written_keys == []
    else:
        assert calls == 4 and invalidated(traces)
        assert len(cache.written_keys) == 2


@pytest.mark.asyncio
async def test_omitting_owner_or_internal_token_disables_cache(monkeypatch):
    world, cache = CacheWorld(), RecordingCache()
    first, _, _ = await run_cached(world, cache, user_id=None)
    assert first.outcome == "ready" and cache.written_keys == []
    monkeypatch.delenv("INTERNAL_TOKEN")
    second, _, _ = await run_cached(world, cache)
    assert second.outcome == "ready" and cache.written_keys == []


def selection():
    return CachedSelection(constraints={"budget_minor": 90000},
                           plan={"options": [{"items": [{"menu_item_id": ITEM_ID, "portions": 1}]}]})


def test_cache_copies_values_and_reading_does_not_extend_ttl():
    clock = [10.0]
    cache = RecommendationCache(capacity=2, ttl_seconds=10, clock=lambda: clock[0])
    original = selection()
    cache.put("key", original)
    original.constraints["budget_minor"] = 1
    original.plan["options"][0]["items"][0]["portions"] = 20
    clock[0] = 19.0
    returned = cache.get("key")
    assert returned.constraints["budget_minor"] == 90000
    assert returned.plan["options"][0]["items"][0]["portions"] == 1
    returned.constraints["budget_minor"] = 2
    assert cache.get("key").constraints["budget_minor"] == 90000
    clock[0] = 20.0
    assert cache.get("key") is None


def test_lru_eviction_uses_access_order_and_put_reclaims_expired_entries():
    clock = [0.0]
    cache = RecommendationCache(capacity=2, ttl_seconds=10, clock=lambda: clock[0])
    cache.put("a", selection())
    cache.put("b", selection())
    assert cache.get("a") is not None
    cache.put("c", selection())
    assert cache.get("b") is None
    assert cache.get("a") is not None and cache.get("c") is not None
    clock[0] = 10.0
    cache.put("d", selection())
    assert cache.get("a") is None and cache.get("c") is None and cache.get("d") is not None
    cache.discard("d")
    cache.discard("missing")
    assert cache.get("d") is None


@pytest.mark.parametrize("options", [{"capacity": 0}, {"ttl_seconds": 0}, {"ttl_seconds": -1}])
def test_cache_rejects_nonpositive_limits(options):
    with pytest.raises(ValueError):
        RecommendationCache(**options)


def test_fingerprint_canonicalizes_dictionary_order_without_normalizing_lists():
    assert fingerprint({"a": 1, "b": [2, 3]}) == fingerprint({"b": [2, 3], "a": 1})
    assert fingerprint({"b": [2, 3]}) != fingerprint({"b": [3, 2]})
    with pytest.raises(ValueError):
        fingerprint({"invalid": float("nan")})
