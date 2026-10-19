from copy import deepcopy
from datetime import timedelta
import re
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import DateTime, create_engine, select, update
from sqlalchemy.orm import sessionmaker

from services.catalog.app import create_app
from services.catalog.core import index_pending, process_import
from services.catalog.embeddings import TestEncoder as FixtureEncoder
from services.catalog.models import DishRow, EvidenceRow, ImportRow, MenuItemRow, MenuVersionRow, RestaurantRow, ReviewRow
from services.catalog import revision as revision_module
from services.common.security import Principal, create_access_token


PATH = "/api/v1/internal/recommendation-revision"
INTERNAL_TOKEN = "explicit-catalog-revision-test-token-longer-than-32"


@pytest.fixture
def catalog_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", INTERNAL_TOKEN)
    monkeypatch.setenv("AUTH_SECRET", "catalog-revision-test-jwt-secret-longer-than-32")
    monkeypatch.setenv("CATALOG_SEED_DEMO", "true")
    engine = create_engine(f"sqlite:///{tmp_path / 'revision.db'}", connect_args={"check_same_thread": False})
    sessions, encoder = sessionmaker(engine, expire_on_commit=False), FixtureEncoder()

    async def profile_loader(_):
        return {"allergies": []}

    app = create_app((engine, sessions), encoder=encoder, profile_loader=profile_loader, start_worker=False)
    with TestClient(app) as client:
        index_pending(sessions, encoder)
        yield client, sessions, encoder, {"X-Internal-Token": INTERNAL_TOKEN}
    engine.dispose()


def digest(client, headers):
    response = client.get(PATH, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"revision"} and re.fullmatch(r"[a-f0-9]{64}", body["revision"])
    assert response.headers["Cache-Control"] == "no-store"
    return body["revision"]


def test_catalog_revision_is_stable_read_only_and_internal(catalog_revision):
    client, sessions, _, headers = catalog_revision
    baseline = digest(client, headers)
    assert digest(client, headers) == baseline
    with sessions() as session:
        assert revision_module.recommendation_revision(session, FixtureEncoder.model_name) == baseline
    assert PATH not in client.get("/openapi.json").json()["paths"]
    for role in ("user", "admin"):
        token = create_access_token(Principal(str(uuid4()), role))
        assert client.get(PATH, headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert client.get(PATH).status_code == 403
    assert client.get(PATH, headers={"X-Internal-Token": "incorrect-test-token"}).status_code == 403


@pytest.mark.parametrize("model,field,mutate", [
    pytest.param(MenuItemRow, "price_minor", lambda value: value + 1, id="menu-price"),
    pytest.param(MenuItemRow, "allergens", lambda value: value + [{"code": "new_allergen", "status": "may_contain"}], id="menu-allergens"),
    pytest.param(MenuItemRow, "ingredients", lambda value: value + ["new ingredient"], id="menu-ingredients"),
    pytest.param(MenuItemRow, "ingredients_complete", lambda value: not value, id="ingredient-completeness"),
    pytest.param(MenuItemRow, "nutrition", lambda value: {**value, "method": (value.get("method") or "") + " changed"}, id="nutrition"),
    pytest.param(MenuItemRow, "embedding", lambda value: [value[0] + 0.25, *value[1:]], id="embedding-values"),
    pytest.param(MenuItemRow, "embedding_model", lambda value: value + "-changed", id="embedding-model"),
    pytest.param(MenuItemRow, "embedding", lambda value: None, id="unindexed-item"),
    pytest.param(RestaurantRow, "active", lambda value: not value, id="restaurant-active"),
    pytest.param(RestaurantRow, "latitude", lambda value: value + 0.01, id="restaurant-location"),
    pytest.param(RestaurantRow, "cuisines", lambda value: value + ["new-cuisine"], id="restaurant-cuisine"),
    pytest.param(ReviewRow, "text", lambda value: value + " Changed", id="review-text"),
    pytest.param(ReviewRow, "rating", lambda value: value - 1, id="review-rating"),
    pytest.param(ReviewRow, "embedding", lambda value: [value[0] + 0.25, *value[1:]], id="review-embedding"),
    pytest.param(EvidenceRow, "fragment_text", lambda value: value + " Changed", id="evidence-text"),
    pytest.param(EvidenceRow, "source_reference", lambda value: value + "-changed", id="evidence-source"),
    pytest.param(ImportRow, "status", lambda value: "needs_review", id="evidence-import-permissions"),
    pytest.param(ImportRow, "file_bytes", lambda value: value + b" changed", id="source-file-bytes"),
    pytest.param(MenuVersionRow, "status", lambda value: "archived", id="menu-version-status"),
    pytest.param(MenuVersionRow, "published_at", lambda value: value + timedelta(seconds=1), id="menu-version-publication"),
    pytest.param(DishRow, "description", lambda value: (value or "") + " Changed", id="dish-description"),
])
def test_catalog_revision_changes_after_relevant_orm_mutations(catalog_revision, model, field, mutate):
    client, sessions, _, headers = catalog_revision
    before = digest(client, headers)
    with sessions() as session:
        row = session.scalar(select(model).order_by(model.id))
        values = {field: mutate(deepcopy(getattr(row, field)))}
        if field != "published_at":
            values.update({column.key: getattr(row, column.key) for column in model.__table__.columns
                           if isinstance(column.type, DateTime)})
        session.execute(update(model).where(model.id == row.id).values(**values))
        session.commit()
    after = digest(client, headers)
    assert after != before
    assert digest(client, headers) == after


def test_review_insert_delete_invalidates_rating_and_count_without_restaurant_update(catalog_revision):
    client, sessions, _, headers = catalog_revision
    before = digest(client, headers)
    review_id = str(uuid4())
    with sessions() as session:
        source = session.scalar(select(ReviewRow).order_by(ReviewRow.id))
        attrs = {column.key: deepcopy(getattr(source, column.key)) for column in ReviewRow.__table__.columns}
        attrs.update(id=review_id, rating=1.0, text="A new source review")
        session.add(ReviewRow(**attrs))
        session.commit()
    assert digest(client, headers) != before
    with sessions() as session:
        session.delete(session.get(ReviewRow, review_id))
        session.commit()
    assert digest(client, headers) == before


def test_menu_publication_and_current_version_switch_change_revision(catalog_revision):
    client, sessions, _, headers = catalog_revision
    admin = {"Authorization": "Bearer " + create_access_token(Principal(str(uuid4()), "admin"))}
    with sessions() as session:
        restaurant = session.scalar(select(RestaurantRow).order_by(RestaurantRow.id))
        restaurant_id, old_version = restaurant.id, restaurant.active_menu_version_id
    response = client.post("/api/v1/menu-imports", headers=admin,
        data={"restaurant_id": restaurant_id, "source_kind": "html"},
        files={"file": ("changed-menu.html", b'<div class="menu-item" data-name="New soup" data-price="300">New soup</div>', "text/html")})
    assert response.status_code == 202, response.text
    import_id = response.json()["id"]
    process_import(sessions, import_id)
    with sessions() as session:
        revision = session.get(ImportRow, import_id).revision
    before_publish = digest(client, headers)
    published = client.post(f"/api/v1/menu-imports/{import_id}/publish", headers=admin, json={"expected_revision": revision})
    assert published.status_code == 201, published.text
    assert published.json()["menu"]["id"] != old_version
    assert digest(client, headers) != before_publish


def test_catalog_code_and_embedding_configuration_invalidate_revision(catalog_revision, monkeypatch):
    client, _, encoder, headers = catalog_revision
    baseline = digest(client, headers)
    with monkeypatch.context() as change:
        change.setattr(revision_module, "SOURCE_REVISION", "0" * 64)
        assert digest(client, headers) != baseline
    assert digest(client, headers) == baseline
    with monkeypatch.context() as change:
        change.setattr(encoder, "model_name", "another-explicit-test-model")
        assert digest(client, headers) != baseline
    assert digest(client, headers) == baseline
    with monkeypatch.context() as change:
        change.setenv("CATALOG_EMBEDDING_PROVIDER", "another-test-provider")
        assert digest(client, headers) != baseline


def test_catalog_source_fingerprint_tracks_bytes_and_names(tmp_path, monkeypatch):
    source = tmp_path / "revision.py"
    source.write_text("first = 1\n")
    monkeypatch.setattr(revision_module, "__file__", str(source))
    before = revision_module._source_revision()
    assert revision_module._source_revision() == before
    source.write_text("first = 2\n")
    assert revision_module._source_revision() != before
    source.write_text("first = 1\n")
    source.rename(tmp_path / "renamed.py")
    assert revision_module._source_revision() != before


def test_revision_fails_closed_when_internal_token_is_missing_or_weak(catalog_revision, monkeypatch):
    client, _, _, _ = catalog_revision
    monkeypatch.delenv("INTERNAL_TOKEN")
    assert client.get(PATH, headers={"X-Internal-Token": INTERNAL_TOKEN}).status_code == 403
    monkeypatch.setenv("INTERNAL_TOKEN", "short")
    assert client.get(PATH, headers={"X-Internal-Token": "short"}).status_code == 403
