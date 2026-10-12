"""Persistence/auth/idempotency/worker tests without configuring a real provider."""
import asyncio
from datetime import timedelta
import importlib
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from services.common.db import make_database
from services.common.security import Principal, create_access_token
from services.recommendations.models import Base, FeedRow, RequestRow, TraceRow, now
from services.recommendations.schemas import RecommendationResult


service = importlib.import_module("services.recommendations.app")
BODY = {"mode": "dish_search", "query": "Хочу хинкали", "max_options": 3}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOMMENDATIONS_DATABASE_URL", f"sqlite:///{tmp_path / 'recommendations.db'}")
    monkeypatch.setenv("AUTH_SECRET", "recommendation-tests-have-a-strong-enough-secret")
    monkeypatch.setenv("RECOMMENDATIONS_WORKER_ENABLED", "false")
    # Enables admission, without ever invoking a real provider in these tests.
    monkeypatch.setenv("LLM_API_KEY", "explicit-test-not-a-live-secret")
    monkeypatch.setenv("LLM_MODEL", "explicit-test-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://model-provider.invalid/v1")
    engine, session_factory = make_database("RECOMMENDATIONS_DATABASE_URL", str(tmp_path / "unused.db"))
    monkeypatch.setattr(service, "engine", engine)
    monkeypatch.setattr(service, "Session", session_factory)
    with TestClient(service.app) as test_client:
        yield test_client


def auth(user_id=None, role="user"):
    return {"Authorization": "Bearer " + create_access_token(Principal(id=user_id or str(uuid4()), role=role))}


def create_run(client, headers, body=None, key="operation-123"):
    return client.post("/api/v1/recommendations", json=body or BODY,
                       headers={**headers, "Idempotency-Key": key})


def no_matches_result():
    return {"id": str(uuid4()), "outcome": "no_matches", "options": [], "clarifications": [],
            "warnings": ["No matching fixtures"], "created_at": now().isoformat()}


def ready_result():
    """Completed recommendation fixture."""
    restaurant_id, item_id, evidence_id, version_id = [str(uuid4()) for _ in range(4)]
    nutrition = {"calories_kcal": 620, "protein_g": 32, "fat_g": 22, "carbs_g": 73.5}
    value = {"id": str(uuid4()), "outcome": "ready", "clarifications": [], "warnings": [],
        "created_at": now().isoformat(), "options": [{
            "id": str(uuid4()), "rank": 1,
            "restaurant": {"id": restaurant_id, "name": "Explicit test restaurant", "address": "Fixture address",
                "location": {"latitude": 55.75, "longitude": 37.61}, "cuisines": ["georgian"],
                "timezone": "Europe/Moscow", "active": True, "active_menu_version_id": version_id,
                "updated_at": now().isoformat(), "is_demo": True},
            "items": [{"menu_item_id": item_id, "dish_id": str(uuid4()), "name": "Test khinkali", "portions": 1,
                "unit_price_minor": 75000, "nutrition_per_portion": {**nutrition, "basis": "per_portion",
                    "source": "restaurant_declared", "method": "Explicit test fixture", "evidence_ids": [evidence_id]}}],
            "total_price_minor": 75000, "currency": "RUB",
            "nutrition": {**nutrition, "known_subtotals": nutrition, "complete_fields": list(nutrition), "sources": ["restaurant_declared"]},
            "reasons": ["Seeded test result"], "warnings": ["Test data"], "evidence_ids": [evidence_id],
        }]}
    return RecommendationResult.model_validate(value).model_dump(mode="json")


def test_auth_and_cross_user_run_context_trace_isolation(client):
    user_id, other_id = str(uuid4()), str(uuid4())
    headers, other = auth(user_id), auth(other_id)
    assert create_run(client, {}).status_code == 401
    assert create_run(client, {"Authorization": "Bearer forged"}).status_code == 401
    response = create_run(client, headers)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert response.headers["Location"].endswith(run_id)
    assert response.headers["Retry-After"] == "2"
    assert client.get(f"/api/v1/recommendations/{run_id}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/recommendations/{run_id}", headers=other).status_code == 404
    assert create_run(client, other, {**BODY, "context_request_id": run_id}, key="other-context-key").status_code == 404
    assert client.get(f"/api/v1/recommendations/{run_id}/trace", headers=headers).status_code == 403
    assert client.get(f"/api/v1/recommendations/{run_id}/trace", headers=auth(role="admin")).status_code == 200


def test_idempotency_is_persistent_scoped_and_conflicting_body_rejected(client):
    headers, other = auth(), auth()
    first = create_run(client, headers)
    assert first.status_code == 202
    same = create_run(client, headers)
    assert same.status_code == 202 and same.json()["id"] == first.json()["id"]
    changed = create_run(client, headers, {**BODY, "query": "Хочу пиццу"})
    assert changed.status_code == 409
    different_owner = create_run(client, other)
    assert different_owner.status_code == 202 and different_owner.json()["id"] != first.json()["id"]
    with service.Session() as db:
        rows = db.scalars(select(RequestRow)).all()
        assert len(rows) == 2
    # Re-entering lifespan simulates restart without a worker and retains queued rows.
    with TestClient(service.app) as restarted:
        persisted = restarted.get(f"/api/v1/recommendations/{first.json()['id']}", headers=headers)
        assert persisted.status_code == 200 and persisted.json()["status"] == "queued"
        assert create_run(restarted, headers).json()["id"] == first.json()["id"]


def test_active_job_limit_and_context_is_snapshot_owned_by_caller(client):
    headers = auth()
    first = create_run(client, headers, key="operation-one").json()
    second = create_run(client, headers, key="operation-two").json()
    assert create_run(client, headers, key="operation-three").status_code == 429
    with service.Session() as db:
        row = db.get(RequestRow, first["id"])
        row.status, row.result = "completed", no_matches_result()
        db.commit()
    follow_up = create_run(client, headers, {**BODY, "context_request_id": first["id"], "query": "А подешевле?"}, key="follow-up-key")
    assert follow_up.status_code == 202, follow_up.text
    with service.Session() as db:
        row = db.get(RequestRow, follow_up.json()["id"])
        assert row.context["query"] == BODY["query"]
        assert row.context["result"]["outcome"] == "no_matches"
        assert row.user_id == db.get(RequestRow, first["id"]).user_id


def test_missing_provider_admission_readiness_and_replay(client, monkeypatch):
    headers = auth()
    original = create_run(client, headers).json()
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert client.get("/health").json()["llm_configured"] is False
    assert client.get("/ready").status_code == 503
    assert create_run(client, headers, key="new-unconfigured-operation").status_code == 503
    replay = create_run(client, headers)
    assert replay.status_code == 202 and replay.json()["id"] == original["id"]


def test_durable_claim_lease_recovery_and_retry_exhaustion(client):
    headers = auth()
    original = create_run(client, headers).json()
    first = service.claim_job()
    assert first[0] == original["id"]
    assert service.claim_job() is None  # No other worker may take an active lease.
    with service.Session() as db:
        row = db.get(RequestRow, first[0])
        assert row.status == "running" and row.attempts == 1
        row.updated_at = now() - timedelta(minutes=16)
        db.commit()
    reclaimed = service.claim_job()
    assert reclaimed[0] == first[0] and reclaimed[4] != first[4]
    with service.Session() as db:
        row = db.get(RequestRow, first[0])
        assert row.attempts == 2
        row.attempts = 3
        row.updated_at = now() - timedelta(minutes=16)
        db.commit()
    assert service.claim_job() is None
    stored = client.get(f"/api/v1/recommendations/{first[0]}", headers=headers).json()
    assert stored["status"] == "failed"
    assert stored["error"]["code"] == "worker_retries_exhausted"


@pytest.mark.asyncio
async def test_worker_persists_typed_result_and_delegates_correct_user(client, monkeypatch):
    user_id = str(uuid4())
    headers = auth(user_id)
    run_id = create_run(client, headers).json()["id"]
    observed = {}

    class InjectedGraph:
        def __init__(self, token, trace):
            observed["claims"] = jwt.decode(token, "recommendation-tests-have-a-strong-enough-secret",
                                            algorithms=["HS256"], audience="gastrogid-api", issuer="gastrogid")
            self.trace = trace

        async def run(self, request, context):
            observed["query"] = request.query
            return no_matches_result()

    monkeypatch.setattr(service, "RecommendationGraph", InjectedGraph)
    task = asyncio.create_task(service.worker())
    try:
        for _ in range(100):
            with service.Session() as db:
                if db.get(RequestRow, run_id).status == "completed":
                    break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Injected worker did not complete durable job")
        response = client.get(f"/api/v1/recommendations/{run_id}", headers=headers).json()
        assert response["status"] == "completed" and response["result"]["outcome"] == "no_matches"
        assert observed["claims"]["sub"] == user_id
        assert observed["claims"]["exp"] - observed["claims"]["iat"] == 1800
        assert observed["query"] == BODY["query"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_failure_is_sanitized_and_cancellation_requeues_job(client, monkeypatch):
    headers = auth()
    failed_id = create_run(client, headers).json()["id"]

    class FailedGraph:
        def __init__(self, token, trace):
            pass

        async def run(self, request, context):
            raise RuntimeError("Provider secret: DO-NOT-PERSIST-THIS-SECRET")

    monkeypatch.setattr(service, "RecommendationGraph", FailedGraph)
    task = asyncio.create_task(service.worker())
    try:
        for _ in range(100):
            with service.Session() as db:
                if db.get(RequestRow, failed_id).status == "failed":
                    break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Worker did not persist failed state")
        response = client.get(f"/api/v1/recommendations/{failed_id}", headers=headers)
        assert response.json()["error"]["code"] == "recommendation_failed"
        assert "DO-NOT-PERSIST-THIS-SECRET" not in response.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    active_id = create_run(client, headers, key="cancelled-operation").json()["id"]
    entered = asyncio.Event()

    class BlockingGraph:
        def __init__(self, token, trace):
            pass

        async def run(self, request, context):
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(service, "RecommendationGraph", BlockingGraph)
    task = asyncio.create_task(service.worker())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    with service.Session() as db:
        row = db.get(RequestRow, active_id)
        assert row.status == "queued" and row.lease_owner is None


def test_feed_visibility_and_status_changes_are_owner_scoped(client):
    user_id = str(uuid4())
    headers, other = auth(user_id), auth()
    empty_run_id = create_run(client, headers).json()["id"]
    with service.Session() as db:
        row = db.get(RequestRow, empty_run_id)
        row.status, row.result = "completed", no_matches_result()
        db.add(FeedRow(user_id=user_id, daily_key="2026-10-09", recommendation_id=empty_run_id,
                       title="No matches is not actionable", expires_at=now() + timedelta(hours=24)))
        db.commit()
    assert client.get("/api/v1/me/feed", headers=headers).json()["items"] == []
    run_id = create_run(client, headers, key="ready-feed-operation").json()["id"]
    with service.Session() as db:
        row = db.get(RequestRow, run_id)
        row.status, row.result = "completed", ready_result()
        feed = FeedRow(user_id=user_id, daily_key="2026-10-11", recommendation_id=run_id,
                       title="Demo recommendation", expires_at=now() + timedelta(hours=24))
        expired = FeedRow(user_id=user_id, daily_key="2026-10-10", recommendation_id=run_id,
                          title="Expired", expires_at=now() - timedelta(hours=1))
        db.add_all([feed, expired])
        db.commit()
        feed_id = feed.id
    assert client.get("/api/v1/me/feed", headers=other).json()["items"] == []
    own_feed = client.get("/api/v1/me/feed", headers=headers).json()
    assert own_feed["total"] == 1 and own_feed["items"][0]["id"] == feed_id
    path = f"/api/v1/me/feed/{feed_id}"
    assert client.patch(path, json={"status": "read"}, headers=other).status_code == 404
    assert client.patch(path, json={"status": "read"}, headers=headers).json()["status"] == "read"
    assert client.patch(path, json={"status": "dismissed"}, headers=headers).status_code == 200
    assert client.get("/api/v1/me/feed", headers=headers).json()["items"] == []
