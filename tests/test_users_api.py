from copy import deepcopy
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from services.common.security import Principal, create_access_token
from services.users.app import create_app
from services.users.models import Account, DiaryEntryRow


ITEM_ID = "20000000-0000-4000-8000-000000000001"
DISH_ID = "00000000-0000-4000-8000-000000000003"
RESTAURANT_ID = "00000000-0000-4000-8000-000000000001"
VERSION_ID = "00000000-0000-4000-8000-000000000002"
MENU_ITEM = {
    "id": ITEM_ID, "dish_id": DISH_ID, "restaurant_id": RESTAURANT_ID,
    "menu_version_id": VERSION_ID, "name": "Demo: khinkali, 4 pcs", "ingredients": ["wheat", "beef"],
    "allergen_info_status": "partial", "allergens": [{"code": "gluten", "status": "contains", "evidence_id": None}],
    "portion_weight_g": 360, "price_minor": 75000, "currency": "RUB",
    "nutrition": {"calories_kcal": 620, "protein_g": 32, "fat_g": 22, "carbs_g": 73.5,
                  "basis": "per_portion", "source": "restaurant_declared",
                  "method": "Test fixture, not real restaurant measurements", "evidence_ids": []},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("USERS_DATABASE_URL", f"sqlite:///{tmp_path / 'users.db'}")
    monkeypatch.setenv("AUTH_SECRET", "users-service-tests-have-a-strong-enough-secret")
    monkeypatch.setenv("INTERNAL_TOKEN", "users-internal-tests-have-a-strong-enough-token")
    monkeypatch.delenv("DEMO_ADMIN_PASSWORD", raising=False)
    application = create_app()
    application.state.catalog_items = {ITEM_ID: deepcopy(MENU_ITEM)}
    application.state.catalog_calls = []
    application.state.catalog_status = 200
    application.state.recommendation_owners = {}

    def catalog(request):
        application.state.catalog_calls.append(request)
        if application.state.catalog_status != 200:
            return httpx.Response(application.state.catalog_status)
        item_id = request.url.path.rsplit("/", 1)[1]
        if "/dishes/" in request.url.path:
            if item_id in {item["dish_id"] for item in application.state.catalog_items.values()}:
                return httpx.Response(200, json={"id": item_id, "restaurant_id": RESTAURANT_ID, "name": "Khinkali"})
            return httpx.Response(404)
        item = application.state.catalog_items.get(item_id)
        return httpx.Response(200, json=item) if item else httpx.Response(404)

    def recommendations(request):
        run_id = request.url.path.rsplit("/", 1)[1]
        user_id = application.state.recommendation_owners.get(run_id)
        if user_id:
            token = request.headers["Authorization"].split(" ", 1)[1]
            claims = jwt.decode(token, "users-service-tests-have-a-strong-enough-secret", algorithms=["HS256"], audience="gastrogid-api", issuer="gastrogid")
            if claims["sub"] == user_id:
                return httpx.Response(200, json={"id": run_id, "status": "completed"})
        return httpx.Response(404)

    application.state.catalog_transport = httpx.MockTransport(catalog)
    application.state.recommendations_transport = httpx.MockTransport(recommendations)
    with TestClient(application) as test_client:
        yield test_client


def account(client, email="student@example.com"):
    response = client.post("/api/v1/auth/register", json={"email": email, "password": "TestPassword123!"})
    assert response.status_code == 201, response.text
    body = response.json()
    return {"Authorization": "Bearer " + body["access_token"]}, body["user"]["id"]


def manual(portions=0.5, when="2026-10-11T10:30:00Z", calories=620, carbs=73.5):
    return {
        "kind": "manual", "name": "Manual meal", "portions": portions, "consumed_at": when,
        "nutrition_per_portion": {"calories_kcal": calories, "protein_g": 32, "fat_g": 22, "carbs_g": carbs},
    }


def create_entry(client, headers, body=None, key="entry-operation-1"):
    return client.post("/api/v1/me/diary", json=body or manual(), headers={**headers, "Idempotency-Key": key})


def test_auth_password_hash_duplicate_login_and_no_role_escalation(client):
    headers, user_id = account(client)
    assert client.get("/api/v1/me/profile", headers=headers).json()["user_id"] == user_id
    assert client.post("/api/v1/auth/register", json={"email": "STUDENT@example.com", "password": "AnotherPassword"}).status_code == 409
    assert client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "missing@example.com", "password": "wrong-password"}).status_code == 401
    response = client.post("/api/v1/auth/login", json={"email": "STUDENT@example.com", "password": "TestPassword123!"})
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "user"
    assert "password" not in response.text
    assert client.post("/api/v1/auth/register", json={"email": "admin@example.com", "password": "AnotherPassword", "role": "admin"}).status_code == 422
    with client.app.state.session_factory() as db:
        stored = db.get(Account, user_id)
        assert stored.password_hash.startswith("$argon2")
        assert "TestPassword123!" not in stored.password_hash


def test_auth_rejects_missing_invalid_and_nonexistent_account(client):
    assert client.get("/api/v1/me/profile").status_code == 401
    assert client.get("/api/v1/me/profile", headers={"Authorization": "Bearer forged"}).status_code == 401
    token = create_access_token(Principal(id=str(uuid4())))
    assert client.get("/api/v1/me/profile", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_profile_validation_null_reset_and_user_isolation(client):
    headers, _ = account(client)
    other, _ = account(client, "other@example.com")
    patch = {"timezone": "Europe/Moscow", "allergies": [{"code": "peanut", "note": "Strict exclusion"}],
             "daily_calories_target": 2200, "disliked_ingredients": ["  кинза  "], "proactive_enabled": True}
    response = client.patch("/api/v1/me/profile", json=patch, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["disliked_ingredients"] == ["кинза"]
    assert client.get("/api/v1/me/profile", headers=other).json()["allergies"] == []
    for invalid in ({}, {"timezone": "Moon/City"}, {"timezone": None}, {"allergies": [{"code": "peanut"}, {"code": "peanut"}]}, {"favorite_cuisines": ["Georgian", "georgian"]}):
        assert client.patch("/api/v1/me/profile", json=invalid, headers=headers).status_code == 422
    assert client.patch("/api/v1/me/profile", json={"daily_calories_target": None}, headers=headers).json()["daily_calories_target"] is None
    updated = client.patch("/api/v1/me/profile", json={"allergies": [{"code": "peanut", "note": "Updated"}, {"code": "gluten"}]}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert {item["code"] for item in updated.json()["allergies"]} == {"peanut", "gluten"}


def test_manual_diary_fractional_unknown_and_remaining(client):
    headers, _ = account(client)
    client.patch("/api/v1/me/profile", json={"daily_calories_target": 200, "daily_protein_target_g": 100}, headers=headers)
    response = create_entry(client, headers)
    assert response.status_code == 201, response.text
    entry = response.json()
    assert entry["consumed_nutrition"]["calories_kcal"] == 310
    assert entry["consumed_nutrition"]["carbs_g"] == 36.75
    assert entry["snapshot"]["nutrition_per_portion"]["source"] == "user_entered"
    summary = client.get("/api/v1/me/daily-summary?date=2026-10-11", headers=headers).json()
    assert summary["remaining"]["calories_kcal"] == -110
    assert summary["remaining"]["protein_g"] == 84
    response = create_entry(client, headers, manual(calories=None, carbs=None), key="unknown-operation")
    assert response.status_code == 201, response.text
    summary = client.get("/api/v1/me/daily-summary?date=2026-10-11", headers=headers).json()
    assert summary["nutrition"]["calories_kcal"] is None
    assert summary["nutrition"]["known_subtotals"]["calories_kcal"] == 310
    assert summary["remaining"]["calories_kcal"] is None
    assert summary["nutrition"]["protein_g"] == 32
    assert summary["warnings"]


def test_idempotency_body_conflicts_owner_scope_and_delete_tombstone(client):
    headers, _ = account(client)
    original = create_entry(client, headers).json()
    equivalent = manual()
    equivalent["portions"] = "0.50"
    equivalent["consumed_at"] = "2026-10-11T13:30:00+03:00"
    repeat = create_entry(client, headers, equivalent)
    assert repeat.status_code == 201, repeat.text
    assert repeat.json()["id"] == original["id"]
    assert client.get("/api/v1/me/diary?date=2026-10-11", headers=headers).json()["total"] == 1
    assert create_entry(client, headers, manual(portions=2)).status_code == 409
    other, _ = account(client, "other@example.com")
    different_owner = create_entry(client, other)
    assert different_owner.status_code == 201
    assert different_owner.json()["id"] != original["id"]
    path = f"/api/v1/me/diary/{original['id']}"
    assert client.patch(path, json={"portions": 2}, headers=other).status_code == 404
    assert client.delete(path, headers=other).status_code == 404
    assert client.delete(path, headers=headers).status_code == 204
    assert create_entry(client, headers).status_code == 409
    assert client.get("/api/v1/me/diary?date=2026-10-11", headers=headers).json()["total"] == 0
    assert client.get("/api/v1/me/daily-summary?date=2026-10-11", headers=headers).json()["nutrition"]["calories_kcal"] == 0
    assert create_entry(client, headers, key="new-operation").status_code == 201
    with client.app.state.session_factory() as db:
        assert db.get(DiaryEntryRow, original["id"]).deleted_at is not None


def test_diary_timezone_boundary_and_explicit_offsets(client):
    headers, _ = account(client)
    client.patch("/api/v1/me/profile", json={"timezone": "Europe/Moscow"}, headers=headers)
    assert create_entry(client, headers, manual(when="2026-10-10T20:59:59Z"), key="yesterday").status_code == 201
    assert create_entry(client, headers, manual(when="2026-10-10T21:00:00Z"), key="today").status_code == 201
    assert create_entry(client, headers, manual(when="2026-10-11T21:00:00Z"), key="tomorrow").status_code == 201
    response = client.get("/api/v1/me/diary?date=2026-10-11", headers=headers)
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["consumed_at"] == "2026-10-10T21:00:00Z"
    assert create_entry(client, headers, manual(when="2026-10-11T10:30:00"), key="no-offset").status_code == 422
    assert client.post("/api/v1/me/diary", json=manual(), headers=headers).status_code == 422


def test_menu_snapshot_survives_catalog_changes_and_portion_patch(client):
    headers, _ = account(client)
    body = {"kind": "menu_item", "menu_item_id": ITEM_ID, "consumed_at": "2026-10-11T10:00:00Z", "portions": 0.5}
    response = create_entry(client, headers, body)
    assert response.status_code == 201, response.text
    entry = response.json()
    assert client.app.state.catalog_calls[-1].headers["Authorization"] == headers["Authorization"]
    client.app.state.catalog_items[ITEM_ID]["name"] = "Changed menu title"
    client.app.state.catalog_items[ITEM_ID]["nutrition"]["calories_kcal"] = 999
    modified = client.patch(f"/api/v1/me/diary/{entry['id']}", json={"portions": 2, "note": "Correction"}, headers=headers)
    assert modified.status_code == 200, modified.text
    assert modified.json()["consumed_nutrition"]["calories_kcal"] == 1240
    assert modified.json()["snapshot"]["name"] == MENU_ITEM["name"]
    assert modified.json()["snapshot"]["nutrition_per_portion"]["calories_kcal"] == 620
    assert create_entry(client, headers, body).json()["id"] == entry["id"]
    assert create_entry(client, headers, body, key="updated-menu-entry").json()["snapshot"]["nutrition_per_portion"]["calories_kcal"] == 999
    with client.app.state.session_factory() as db:
        row = db.get(DiaryEntryRow, entry["id"])
        assert row.snapshot.data["nutrition_per_portion"]["carbs_g"] == "73.5"


def test_meal_calculation_exact_prices_unknown_and_same_version_requirement(client):
    headers, _ = account(client)
    second_id = str(uuid4())
    second = deepcopy(MENU_ITEM)
    second["id"] = second_id
    second["price_minor"] = 101
    second["nutrition"]["calories_kcal"] = None
    client.app.state.catalog_items[second_id] = second
    response = client.post("/api/v1/nutrition/calculate", headers=headers,
                           json={"items": [{"menu_item_id": ITEM_ID, "portions": 0.5}, {"menu_item_id": second_id, "portions": 0.5}]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["price_minor"] == 37551
    assert result["nutrition"]["calories_kcal"] is None
    assert result["nutrition"]["known_subtotals"]["calories_kcal"] == 310
    assert result["nutrition"]["protein_g"] == 32
    assert result["warnings"]
    second["menu_version_id"] = str(uuid4())
    assert client.post("/api/v1/nutrition/calculate", headers=headers,
                       json={"items": [{"menu_item_id": ITEM_ID, "portions": 1}, {"menu_item_id": second_id, "portions": 1}]}).status_code == 422


def test_catalog_failure_no_write_and_same_key_can_retry(client):
    headers, _ = account(client)
    body = {"kind": "menu_item", "menu_item_id": ITEM_ID, "consumed_at": "2026-10-11T10:00:00Z", "portions": 1}
    client.app.state.catalog_status = 503
    assert create_entry(client, headers, body).status_code == 503
    assert client.get("/api/v1/me/diary?date=2026-10-11", headers=headers).json()["total"] == 0
    client.app.state.catalog_status = 200
    assert create_entry(client, headers, body).status_code == 201


def test_internal_context_opt_in_allergies_short_lived_jwt(client, monkeypatch):
    headers, user_id = account(client)
    account(client, "not-opted-in@example.com")
    client.patch("/api/v1/me/profile", headers=headers,
                 json={"proactive_enabled": True, "allergies": [{"code": "peanut"}, {"code": "gluten"}]})
    assert client.get("/api/v1/internal/users-contexts").status_code == 403
    assert client.get("/api/v1/internal/users-contexts", headers=headers).status_code == 403
    response = client.get("/api/v1/internal/users-contexts", headers={"X-Internal-Token": "users-internal-tests-have-a-strong-enough-token"})
    assert response.status_code == 200, response.text
    contexts = response.json()["items"]
    assert len(contexts) == 1 and contexts[0]["user_id"] == user_id
    assert {value["code"] for value in contexts[0]["profile"]["allergies"]} == {"peanut", "gluten"}
    claims = jwt.decode(contexts[0]["jwt"], "users-service-tests-have-a-strong-enough-secret", algorithms=["HS256"], audience="gastrogid-api", issuer="gastrogid")
    assert claims["exp"] - claims["iat"] == 300
    assert "password" not in response.text
    assert client.get("/api/v1/me/profile", headers={"Authorization": "Bearer " + contexts[0]["jwt"]}).status_code == 200


def test_feedback_upsert_scope_and_recommendation_ownership(client):
    headers, user_id = account(client)
    other, other_id = account(client, "other@example.com")
    body = {"dish_id": DISH_ID, "rating": "like", "comment": "Nice"}
    original = client.post("/api/v1/me/feedback", json=body, headers=headers)
    assert original.status_code == 200, original.text
    changed = client.post("/api/v1/me/feedback", json={**body, "rating": "dislike"}, headers=headers)
    assert changed.json()["id"] == original.json()["id"]
    assert client.get("/api/v1/me/feedback", headers=headers).json()["items"][0]["rating"] == "dislike"
    assert client.get("/api/v1/me/feedback", headers=other).json()["items"] == []
    run_id = str(uuid4())
    client.app.state.recommendation_owners[run_id] = other_id
    assert client.post("/api/v1/me/feedback", json={**body, "recommendation_id": run_id}, headers=headers).status_code == 404
    client.app.state.recommendation_owners[run_id] = user_id
    assert client.post("/api/v1/me/feedback", json={**body, "recommendation_id": run_id}, headers=headers).status_code == 200
    assert client.post("/api/v1/me/feedback", json={"dish_id": str(uuid4()), "rating": "like"}, headers=headers).status_code == 404


def test_database_persists_accounts_and_diary_between_app_instances(client):
    headers, user_id = account(client)
    entry = create_entry(client, headers).json()
    another = create_app()
    with TestClient(another) as restarted:
        assert restarted.get("/api/v1/me/profile", headers=headers).json()["user_id"] == user_id
        diary = restarted.get("/api/v1/me/diary?date=2026-10-11", headers=headers).json()
        assert diary["items"][0]["id"] == entry["id"]


def test_admin_seed_is_explicit_and_works_with_test_email(tmp_path, monkeypatch):
    monkeypatch.setenv("USERS_DATABASE_URL", f"sqlite:///{tmp_path / 'seed.db'}")
    monkeypatch.setenv("AUTH_SECRET", "users-service-tests-have-a-strong-enough-secret")
    monkeypatch.delenv("DEMO_ADMIN_PASSWORD", raising=False)
    application = create_app()
    with TestClient(application):
        with application.state.session_factory() as db:
            assert db.scalars(select(Account)).all() == []
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "ExplicitDemoPassword!")
    monkeypatch.delenv("DEMO_ADMIN_EMAIL", raising=False)
    application = create_app()
    with TestClient(application) as seeded:
        response = seeded.post("/api/v1/auth/login", json={"email": "admin@example.test", "password": "ExplicitDemoPassword!"})
        assert response.status_code == 200, response.text
        assert response.json()["user"]["role"] == "admin"


def test_missing_auth_secret_readiness_has_useful_error(client, monkeypatch):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    assert client.post("/api/v1/auth/register", json={"email": "student@example.com", "password": "TestPassword123!"}).status_code == 503
    with client.app.state.session_factory() as db:
        assert db.scalars(select(Account)).all() == []
