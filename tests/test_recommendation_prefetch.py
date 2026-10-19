from copy import deepcopy
import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from services.recommendations.graph import PipelineError, RecommendationGraph
from test_recommendations_graph import EVIDENCE_ID, ITEM_ID, RESTAURANT_ID, World, message


def search_state(mode="meal_plan"):
    return {"candidates": {}, "constraints": {"min_protein_g": 50, "budget_minor": 90000},
            "request": {"mode": mode}, "search_query": "Запрос, уточнённый координатором", "search_count": 0}


def backend_retrieval(messages):
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage) and isinstance(messages[1], HumanMessage)
    assert not any(isinstance(item, (AIMessage, ToolMessage)) for item in messages)
    return json.loads(messages[-1].content.split("\n")[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["meal_plan", "dish_search"])
async def test_prefetch_is_real_retrieval_and_search_llm_can_accept_without_extra_tool_calls(mode):
    class InspectingModel:
        calls = 0

        def bind_tools(self, tools, **kwargs):
            assert {item.name for item in tools} == {"search_menu", "restaurant_reviews"}
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            data = backend_retrieval(messages)
            assert data["source"] == "backend_retrieval" and data["tool"] == "search_menu"
            assert data["query"] == "Запрос, уточнённый координатором"
            assert data["result"]["items"][0]["menu_item_id"] == "m1"
            assert data["result"]["items"][0]["evidence_ids"] == ["e1"]
            assert data["result"]["restaurants"][0]["id"] == "r1"
            return AIMessage(content="DONE", usage_metadata={"input_tokens": 101, "output_tokens": 2, "total_tokens": 103})

    world, model, traces = World(), InspectingModel(), []
    state = search_state(mode)
    original = deepcopy(state)
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.respond)) as client:
        graph = RecommendationGraph("test-token", traces.append, model=model, client=client)
        update = await graph.traced("search", graph.search, state)
    searches = [request for request in world.calls if request.url.path.endswith("/search")]
    assert len(searches) == 1 and model.calls == 1
    payload = json.loads(searches[0].content)
    assert payload["query"] == state["search_query"] and payload["limit"] == 20
    assert payload["constraints"]["budget_minor"] == 90000
    assert payload["constraints"]["min_protein_g"] == (None if mode == "meal_plan" else 50)
    assert state == original
    assert update["candidates"][ITEM_ID]["item"] == world.item
    assert update["candidates"][ITEM_ID]["evidence_ids"] == [EVIDENCE_ID]
    assert update["search_count"] == 1
    assert traces[0]["tool_names"] == ["search_menu"]
    assert traces[0]["input_tokens"] == 101 and traces[0]["output_tokens"] == 2
    assert "llm_calls=1" in traces[0]["summary"]
    assert "prefetched_search=1" in traces[0]["summary"]


@pytest.mark.asyncio
async def test_empty_prefetch_can_be_refined_by_the_search_llm():
    class RefiningModel:
        calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                assert backend_retrieval(messages)["result"]["items"] == []
                return message("search_menu", {"query": "Более точный поиск"})
            assert isinstance(messages[-1], ToolMessage)
            assert json.loads(messages[-1].content)["items"][0]["menu_item_id"] == "m1"
            return AIMessage(content="DONE")

    world, model, requests = World(), RefiningModel(), []

    def respond(request):
        if request.url.path.endswith("/search"):
            requests.append(request)
            world.empty_search = len(requests) == 1
        return world.respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        graph = RecommendationGraph("test-token", lambda _: None, model=model, client=client)
        graph.metrics = {"input_tokens": 0, "output_tokens": 0, "tool_names": []}
        update = await graph.search(search_state())
    assert model.calls == 2 and len(requests) == 2
    assert json.loads(requests[1].content)["query"] == "Более точный поиск"
    assert set(update["candidates"]) == {ITEM_ID}


@pytest.mark.asyncio
async def test_unknown_review_reference_is_rejected_and_known_reference_can_recover():
    class ReviewingModel:
        calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                backend_retrieval(messages)
                return message("restaurant_reviews", {"restaurant_id": "r999"})
            if self.calls == 2:
                assert json.loads(messages[-1].content) == {"error": "invalid_tool_input"}
                return message("restaurant_reviews", {"restaurant_id": "r1"})
            assert json.loads(messages[-1].content) == {"items": [], "total": 0}
            return AIMessage(content="DONE")

    world, model, reviews = World(), ReviewingModel(), []

    def respond(request):
        if request.url.path.endswith("/reviews"):
            reviews.append(request.url.path)
            return httpx.Response(200, json={"items": [], "total": 0})
        return world.respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        graph = RecommendationGraph("test-token", lambda _: None, model=model, client=client)
        graph.metrics = {"input_tokens": 0, "output_tokens": 0, "tool_names": []}
        update = await graph.search(search_state())
    assert reviews == [f"/api/v1/restaurants/{RESTAURANT_ID}/reviews"]
    assert model.calls == 3 and ITEM_ID in update["candidates"]


@pytest.mark.asyncio
async def test_prefetch_dependency_failure_does_not_create_a_synthetic_search_acknowledgement():
    class MustNotRun:
        def bind_tools(self, *args, **kwargs):
            raise AssertionError("Failed retrieval must propagate before invoking the model")

    world, traces = World(), []
    world.search_status = 503
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.respond)) as client:
        graph = RecommendationGraph("test-token", traces.append, model=MustNotRun(), client=client)
        with pytest.raises(PipelineError) as failure:
            await graph.traced("search", graph.search, search_state())
    assert failure.value.code == "dependency_unavailable"
    assert traces[0]["status"] == "failed" and "llm_calls=0" in traces[0]["summary"]
