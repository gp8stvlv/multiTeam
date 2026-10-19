import asyncio
from copy import deepcopy
from datetime import date

import httpx
import pytest

from services.recommendations.graph import PipelineError, RecommendationGraph
from services.recommendations.schemas import RecommendationCreate


PROFILE = {
    "timezone": "Europe/Moscow",
    "allergies": [{"code": "peanut"}],
    "disliked_ingredients": ["onion"],
    "spice_preference": "none",
    "default_budget_minor": 90000,
}
SUMMARY = {"entry_count": 2, "remaining": {"calories_kcal": 1400}}
FEEDBACK = {"items": [{"dish_id": "liked-dish", "rating": "like"}]}


@pytest.mark.asyncio
async def test_context_reads_independent_user_data_concurrently_and_preserves_constraints():
    summary_started, feedback_started = asyncio.Event(), asyncio.Event()
    calls = []

    async def respond(request):
        calls.append(request)
        if request.url.path.endswith("/me/profile"):
            return httpx.Response(200, json=PROFILE)
        assert calls[0].url.path.endswith("/me/profile")
        if request.url.path.endswith("/me/daily-summary"):
            summary_started.set()
            await feedback_started.wait()
            date.fromisoformat(request.url.params["date"])
            return httpx.Response(200, json=SUMMARY)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"items": [], "warnings": ["Indexing in progress"]})
        assert request.url.path.endswith("/me/feedback")
        feedback_started.set()
        await summary_started.wait()
        return httpx.Response(200, json=FEEDBACK)

    state = {
        "request": RecommendationCreate(mode="meal_plan", query="Обед", constraints={
            "budget_minor": 60000, "excluded_allergen_codes": ["gluten"],
            "excluded_ingredients": ["cilantro"],
        }).model_dump(mode="json"),
        "context": {"previous_request": "unchanged"},
    }
    original = deepcopy(state)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        graph = RecommendationGraph("context-test-token", lambda _: None, model=object(), client=client)
        result = await asyncio.wait_for(graph.context(state), timeout=3)

    assert result["context"] == {
        "profile": PROFILE, "daily_summary": SUMMARY, "feedback": FEEDBACK,
        "previous": state["context"],
    }
    assert result["constraints"]["budget_minor"] == 60000
    assert result["constraints"]["excluded_allergen_codes"] == ["gluten", "peanut"]
    assert result["constraints"]["excluded_ingredients"] == ["cilantro", "onion"]
    assert result["constraints"]["spice_preference"] == "none"
    assert result["candidates"] == {} and result["inspections"] == []
    assert result["iterations"] == result["search_count"] == result["nutrition_count"] == result["planner_count"] == 0
    assert result["rejected"] == [] and state == original
    assert len(calls) == 4
    assert all(request.headers["Authorization"] == "Bearer context-test-token" for request in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_path", ["profile", "daily-summary", "feedback"])
async def test_context_dependency_failure_remains_an_error(failed_path):
    calls = []
    data = {"profile": PROFILE, "daily-summary": SUMMARY, "feedback": FEEDBACK}

    async def respond(request):
        path = request.url.path.rsplit("/", 1)[-1]
        calls.append(path)
        return httpx.Response(503) if path == failed_path else httpx.Response(200, json=data[path])

    state = {"request": RecommendationCreate(mode="meal_plan", query="Обед").model_dump(mode="json")}
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        graph = RecommendationGraph("context-test-token", lambda _: None, model=object(), client=client)
        with pytest.raises(PipelineError) as failure:
            await graph.context(state)

    assert failure.value.code == "dependency_unavailable"
    if failed_path == "profile":
        assert calls == ["profile"]
    else:
        assert set(calls) == set(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_path", ["daily-summary", "feedback"])
async def test_context_failure_cancels_and_awaits_inflight_sibling(failed_path):
    sibling_started, sibling_cleaned = asyncio.Event(), asyncio.Event()
    sibling_tasks = []

    async def respond(request):
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "profile":
            return httpx.Response(200, json=PROFILE)
        if path == failed_path:
            await sibling_started.wait()
            return httpx.Response(503)
        sibling_tasks.append(asyncio.current_task())
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            sibling_cleaned.set()

    state = {"request": RecommendationCreate(mode="meal_plan", query="Обед").model_dump(mode="json")}
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        graph = RecommendationGraph("context-test-token", lambda _: None, model=object(), client=client)
        with pytest.raises(PipelineError) as failure:
            await asyncio.wait_for(graph.context(state), timeout=3)
        assert failure.value.code == "dependency_unavailable"
        assert sibling_cleaned.is_set()
        assert len(sibling_tasks) == 1 and sibling_tasks[0].done() and sibling_tasks[0].cancelled()


@pytest.mark.asyncio
async def test_context_cancellation_awaits_both_inflight_requests():
    started = {path: asyncio.Event() for path in ("daily-summary", "feedback")}
    cleaned = {path: asyncio.Event() for path in started}
    lookup_tasks = []

    async def respond(request):
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "profile":
            return httpx.Response(200, json=PROFILE)
        lookup_tasks.append(asyncio.current_task())
        started[path].set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned[path].set()

    state = {"request": RecommendationCreate(mode="meal_plan", query="Обед").model_dump(mode="json")}
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        graph = RecommendationGraph("context-test-token", lambda _: None, model=object(), client=client)
        context_task = asyncio.create_task(graph.context(state))
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), timeout=3)
        finally:
            context_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await context_task
        assert all(event.is_set() for event in cleaned.values())
        assert len(lookup_tasks) == 2 and all(task.done() and task.cancelled() for task in lookup_tasks)
