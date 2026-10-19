"""Real ChatOpenAI HTTP adapter contract, with only the remote wire mocked."""
import json
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import HumanMessage

from services.recommendations.graph import RecommendationGraph
from services.recommendations.provider import ProviderNotConfigured, create_model
from services.recommendations.schemas import CoordinatorDecision, RecommendationCreate, RecommendationResult


TEST_KEY = "explicit-provider-test-key-only-not-a-real-secret"
PRIVATE_REASONING = "Explicit fixture-only hidden reasoning: " + TEST_KEY
CODING_URL = "https://api.z.ai/api/coding/paas/v4"


@pytest.fixture
def provider_environment(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", TEST_KEY)
    monkeypatch.setenv("LLM_MODEL", "glm-5.3-flash")
    monkeypatch.setenv("LLM_BASE_URL", CODING_URL)
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)


def completion(tool_name=None, arguments=None):
    assistant = {"role": "assistant", "content": None if tool_name else "Done",
                 "reasoning_content": PRIVATE_REASONING}
    if tool_name:
        assistant["tool_calls"] = [{"id": "call-" + str(uuid4()), "type": "function",
            "function": {"name": tool_name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]
    return {"id": "chatcmpl-explicit-fixture", "object": "chat.completion", "created": 1791738000,
        "model": "glm-5.3-flash", "choices": [{"index": 0, "message": assistant,
            "finish_reason": "tool_calls" if tool_name else "stop"}],
        "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}}


@pytest.mark.parametrize("missing", ["LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"])
def test_missing_provider_setting_rejects_creation_without_secret_echo(provider_environment, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(ProviderNotConfigured) as failure:
        create_model()
    assert TEST_KEY not in str(failure.value)


@pytest.mark.asyncio
async def test_glm_coding_payload_and_structured_tool_call_use_real_adapter(provider_environment):
    observed = []

    def provider(request):
        observed.append(request)
        return httpx.Response(200, json=completion("CoordinatorDecision", {
            "action": "search", "search_query": "Хинкали", "additional_constraints": {"budget_minor": 90000},
        }))

    transport = httpx.MockTransport(provider)
    with httpx.Client(transport=transport) as sync_client:
        async with httpx.AsyncClient(transport=transport) as async_client, httpx.AsyncClient() as services_client:
            model = create_model(http_client=sync_client, http_async_client=async_client)
            graph = RecommendationGraph("delegated-user-token-fixture", lambda _: None,
                                        model=model, client=services_client)
            graph.metrics = {"input_tokens": 0, "output_tokens": 0, "tool_names": []}
            decision = await graph.structured(CoordinatorDecision, "Explicit fixture: choose search.")
            assert decision.action == "search"
            assert decision.additional_constraints.budget_minor == 90000
            assert graph.metrics["input_tokens"] == 13 and graph.metrics["output_tokens"] == 7

    assert len(observed) == 1
    request = observed[0]
    payload = json.loads(request.content)
    assert str(request.url) == CODING_URL + "/chat/completions"
    assert request.headers["Authorization"] == "Bearer " + TEST_KEY
    assert payload["model"] == "glm-5.3-flash"
    assert payload["reasoning_effort"] == "low"
    assert payload["max_tokens"] == 6000
    assert "max_completion_tokens" not in payload
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["function"]["name"] == "CoordinatorDecision"
    assert not any(TEST_KEY in message["content"] for message in payload["messages"])
    serialized = decision.model_dump_json()
    assert TEST_KEY not in serialized and PRIVATE_REASONING not in serialized


@pytest.mark.asyncio
async def test_openrouter_keeps_standard_openai_payload(provider_environment, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "z-ai/glm-5.3-flash")
    observed = []

    def provider(request):
        observed.append(request)
        return httpx.Response(200, json=completion("CoordinatorDecision", {"action": "search"}))

    transport = httpx.MockTransport(provider)
    with httpx.Client(transport=transport) as sync_client:
        async with httpx.AsyncClient(transport=transport) as async_client:
            model = create_model(http_client=sync_client, http_async_client=async_client)
            response = await model.bind_tools([CoordinatorDecision], tool_choice="auto").ainvoke([
                HumanMessage(content="Return the search decision through the typed tool.")])
            assert CoordinatorDecision.model_validate(response.tool_calls[0]["args"]).action == "search"
    payload = json.loads(observed[0].content)
    assert str(observed[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert payload["max_completion_tokens"] == 6000
    assert "max_tokens" not in payload
    assert payload["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_glm_explicit_token_and_reasoning_configuration_are_applied(provider_environment, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "8000")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
    payloads = []

    def provider(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    transport = httpx.MockTransport(provider)
    with httpx.Client(transport=transport) as sync_client:
        async with httpx.AsyncClient(transport=transport) as async_client:
            model = create_model(http_client=sync_client, http_async_client=async_client)
            await model.ainvoke([HumanMessage(content="Explicit configuration fixture")])
    assert payloads[0]["max_tokens"] == 8000 and payloads[0]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_real_adapter_full_graph_tools_cross_services_and_no_secret_persistence(provider_environment):
    # Keep SDK serialization and tool calling; mock only HTTP transport.
    from test_recommendations_graph import ITEM_ID, World

    world = World()
    provider_requests, traces = [], []
    coordinator_count = 0

    def provider(request):
        nonlocal coordinator_count
        provider_requests.append(request)
        payload = json.loads(request.content)
        assert payload.get("tool_choice", "auto") == "auto"
        assert payload["max_tokens"] == 6000
        assert payload["reasoning_effort"] == "low"
        first_tool = payload["tools"][0]["function"]["name"]
        if first_tool == "CoordinatorDecision":
            action = ("search", "nutrition", "planner", "finish")[coordinator_count]
            coordinator_count += 1
            body = completion(first_tool, {"action": action})
        elif first_tool == "MealPlan":
            body = completion(first_tool, {"options": [{"items": [{"menu_item_id": "m1", "portions": 1}],
                "reason_codes": ["query_match", "budget_fit"]}]})
        elif payload["messages"][-1]["role"] == "tool":
            body = completion()
        elif first_tool == "search_menu":
            body = completion(first_tool, {"query": "Хочу хинкали"})
        elif first_tool == "calculate_meal":
            body = completion(first_tool, {"menu_item_ids": ["m1"]})
        else:
            raise AssertionError("Unexpected adapter tool binding")
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(provider)
    with httpx.Client(transport=transport) as sync_client:
        async with httpx.AsyncClient(transport=transport) as async_client:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.respond)) as services_client:
                model = create_model(http_client=sync_client, http_async_client=async_client)
                graph = RecommendationGraph("delegated-user-token-fixture", traces.append,
                    model=model, client=services_client)
                result = await graph.run(RecommendationCreate(mode="dish_search", query="Хочу хинкали"))
    checked = RecommendationResult.model_validate(result)
    assert checked.outcome == "ready" and checked.options[0].total_price_minor == 75000
    assert str(checked.options[0].items[0].menu_item_id) == ITEM_ID
    assert len(provider_requests) == 5
    assert coordinator_count == 1
    nutrition_requests = [request for request in provider_requests
                          if json.loads(request.content)["tools"][0]["function"]["name"] == "calculate_meal"]
    assert len(nutrition_requests) == 1
    assert {trace["agent"] for trace in traces} == {"coordinator", "search", "nutrition", "planner", "validator"}
    persisted = json.dumps({"result": result, "traces": traces}, ensure_ascii=False)
    assert TEST_KEY not in persisted and PRIVATE_REASONING not in persisted
    assert "reasoning_content" not in persisted
    assert all(request.headers["Authorization"] == "Bearer delegated-user-token-fixture" for request in world.calls)
