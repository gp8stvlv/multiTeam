from __future__ import annotations

from io import BytesIO
import os
import shutil
from uuid import uuid4

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from services.common.security import Principal, create_access_token
from services.catalog.app import create_app
from services.catalog.core import CatalogWorker, index_pending, process_import
from services.catalog.embeddings import FastEmbedEncoder, TestEncoder, configured_encoder
from services.catalog.models import EvidenceRow, ImportRow, MenuItemRow, MenuVersionRow, RestaurantRow
from services.catalog.schemas import NutritionFacts
from services.catalog.seed import demo_id


HTML = b'''<html><body><div class="menu-item" data-name="Mushroom dumplings"
 data-price="450" data-weight-g="220" data-ingredients="wheat flour,mushrooms,onion"
 data-ingredients-complete="true" data-tags="main,mild,vegetarian"
 data-allergens="wheat:contains;peanut:declared_absent" data-allergen-status="partial"
 data-nutrition-basis="per_portion" data-kcal="380" data-protein-g="12"
 data-fat-g="10" data-carbs-g="60">Mushroom dumplings | 450 RUB | 220 g</div>
 <div class="menu-item" data-name="Unknown soup" data-price="200">Unknown soup | 200 RUB</div>
 </body></html>'''


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "catalog-test-secret-long-enough-for-jwt-signing")
    monkeypatch.setenv("CATALOG_SEED_DEMO", "true")
    engine = create_engine(f"sqlite:///{tmp_path/'catalog.db'}", connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def enable_fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    sessions = sessionmaker(engine, expire_on_commit=False)
    profile = {"allergies": []}
    async def loader(_):
        return profile
    encoder = TestEncoder()
    application = create_app((engine, sessions), encoder=encoder, profile_loader=loader, start_worker=False)
    user = {"Authorization": "Bearer " + create_access_token(Principal(str(uuid4()), "user"))}
    admin = {"Authorization": "Bearer " + create_access_token(Principal(str(uuid4()), "admin"))}
    with TestClient(application) as client:
        index_pending(sessions, encoder)
        yield client, sessions, encoder, user, admin, profile
    engine.dispose()


def upload(client, admin, content=HTML, kind="html", filename="menu.html"):
    response = client.post("/api/v1/menu-imports", headers=admin,
        data={"restaurant_id": demo_id("restaurant-0"), "source_kind": kind},
        files={"file": (filename, content, "application/octet-stream")})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_seed_persistent_catalog_and_auth(catalog):
    client, sessions, _, user, admin, _ = catalog
    assert client.get("/api/v1/restaurants").status_code == 401
    restaurants = client.get("/api/v1/restaurants", headers=user).json()
    assert restaurants["total"] == 5
    assert all(x["is_demo"] and x["review_count"] == 2 for x in restaurants["items"])
    with sessions() as session:
        assert len(session.scalars(select(MenuItemRow)).all()) == 60
    menu = client.get(f'/api/v1/restaurants/{demo_id("restaurant-0")}/menu', headers=user).json()
    assert len(menu["items"]) == 12
    item = menu["items"][0]
    assert client.get(f'/api/v1/dishes/{item["dish_id"]}', headers=user).status_code == 200
    evidence = client.get(f'/api/v1/evidence/{item["evidence_ids"][0]}', headers=user).json()
    assert evidence["is_demo"] and evidence["menu_item_id"] == item["id"]
    assert client.get(f'/api/v1/evidence/{evidence["id"]}/source', headers=user).headers["x-content-type-options"] == "nosniff"
    assert client.post("/api/v1/restaurants", headers=user, json={}).status_code == 403
    assert client.get("/api/v1/restaurants?sort=distance", headers=user).status_code == 422


def test_search_never_bypasses_allergies_or_unknown_nutrition(catalog):
    client, sessions, encoder, user, _, profile = catalog
    profile["allergies"] = [{"code": "wheat", "note": None}]
    result = client.post("/api/v1/search", headers=user, json={"query": "хинкали", "constraints": {"excluded_allergen_codes": [], "max_calories_kcal": 700}, "limit": 50})
    assert result.status_code == 200, result.text
    hits = result.json()["items"]
    assert hits
    for hit in hits:
        item = hit["item"]
        assert item["nutrition"]["calories_kcal"] is not None
        assert item["nutrition"]["calories_kcal"] <= 700
        assert any(x["code"] == "wheat" and x["status"] == "declared_absent" and x["evidence_id"] for x in item["allergens"])
        for evidence_id in hit["evidence_ids"]:
            assert client.get(f"/api/v1/evidence/{evidence_id}", headers=user).status_code == 200
    # Absence of a declaration is not evidence of absence, even on complete menus.
    profile["allergies"] = [{"code": "rare_allergen", "note": None}]
    assert client.post("/api/v1/search", headers=user, json={"query": "обед"}).json()["items"] == []


def test_exact_food_intent_reranks_imperfect_cosine_candidates(catalog):
    """The lexical bonus must be transparent and cannot overwrite cosine facts."""
    import math
    client, sessions, encoder, user, _, _ = catalog
    query_vector = encoder.encode(["хинкали"])[0]
    index = query_vector.index(max(query_vector))
    other = (index+1) % len(query_vector)
    with sessions() as session:
        for item in session.scalars(select(MenuItemRow)):
            similarity = 0.4 if "Хинкали" in item.name else (1.0 if item.name == "Чурчхела" else 0.1)
            vector = [0.0] * len(query_vector)
            vector[index], vector[other] = similarity, math.sqrt(1-similarity**2)
            item.embedding = vector
        session.commit()
    result = client.post("/api/v1/search", headers=user, json={"query": "хочу хинкали", "limit": 5})
    assert result.status_code == 200, result.text
    hits = result.json()["items"]
    assert all("Хинкали" in x["item"]["name"] for x in hits[:2])
    assert hits[0]["cosine_similarity"] == 0.4
    assert hits[0]["relevance_score"] == 0.4
    assert hits[0]["ranking_score"] > hits[2]["ranking_score"]
    assert hits[0]["matched_query_terms"] == ["хинкали"]
    assert hits[0]["ranking_method"] == "cosine_plus_explicit_name_tag_bonus"
    profile_query = {"query": "Хочу хинкалей", "constraints": {"budget_minor": 65000}, "limit": 3}
    hits = client.post("/api/v1/search", headers=user, json=profile_query).json()["items"]
    assert hits[0]["item"]["name"] == "Хинкали с грибами, 4 шт."
    assert all(x["item"]["price_minor"] <= 65000 for x in hits)


def test_hybrid_search_does_not_fallback_when_encoder_fails(catalog):
    from fastapi import HTTPException
    from services.catalog.core import semantic_search
    from services.catalog.schemas import SearchRequest
    _, sessions, _, _, _, _ = catalog
    class FailedEncoder:
        model_name = TestEncoder.model_name
        def encode(self, _):
            raise RuntimeError("Model failed")
    with sessions() as session, pytest.raises(HTTPException) as failure:
        semantic_search(session, SearchRequest(query="хочу хинкали"), FailedEncoder())
    assert failure.value.status_code == 503


@pytest.mark.parametrize("tags,none_allowed,mild_allowed", [
    (["not_spicy"], True, True),
    (["mild"], False, True),
    (["spicy"], False, False),
    ([], False, False),
    (["not_spicy", "spicy"], False, False),
    (["not_spicy", "mild"], False, True),
])
def test_spice_constraints_require_explicit_consistent_tags(catalog, tags, none_allowed, mild_allowed):
    from services.catalog.core import filter_item
    from services.catalog.schemas import Constraints
    _, sessions, _, _, _, _ = catalog
    with sessions() as session:
        item = session.get(MenuItemRow, demo_id("item-0-0"))
        restaurant = session.get(RestaurantRow, item.restaurant_id)
        item.tags = tags
        assert filter_item(item, restaurant, Constraints(spice_preference="none")) is none_allowed
        assert filter_item(item, restaurant, Constraints(spice_preference="mild")) is mild_allowed
        assert filter_item(item, restaurant, Constraints(spice_preference="any")) is True


@pytest.mark.skipif(os.getenv("CATALOG_RUN_MODEL_TESTS") != "1", reason="Opt-in real ONNX inference; requires downloaded model cache")
def test_real_multilingual_model_food_intent(catalog):
    client, sessions, _, user, _, _ = catalog
    encoder = FastEmbedEncoder()
    index_pending(sessions, encoder, force=True)
    async def profile_loader(_):
        return {"allergies": []}
    application = create_app((sessions.kw["bind"], sessions), encoder=encoder, profile_loader=profile_loader, seed=False, start_worker=False)
    with TestClient(application) as real_client:
        for query in ["хочу хинкали", "Хочу хинкалей", "khinkali"]:
            result = real_client.post("/api/v1/search", headers=user, json={"query": query, "limit": 5})
            assert result.status_code == 200, result.text
            hits = result.json()["items"]
            # Russian exact intent is covered by hybrid signals; English by real multilingual semantics.
            assert any("Хинкали" in x["item"]["name"] for x in hits), (query, [x["item"]["name"] for x in hits])
            assert result.json()["embedding_model"] == encoder.model_name


def test_import_review_publish_immutable_history(catalog):
    client, sessions, encoder, user, admin, _ = catalog
    old_menu = client.get(f'/api/v1/restaurants/{demo_id("restaurant-0")}/menu', headers=user).json()
    job_id = upload(client, admin)
    process_import(sessions, job_id)
    draft = client.get(f"/api/v1/menu-imports/{job_id}/draft", headers=admin).json()
    assert len(draft["items"]) == 2
    known, unknown = draft["items"]
    assert known["price_minor"] == 45000
    assert known["nutrition"]["calories_kcal"] == 380
    assert unknown["ingredients"] == [] and unknown["nutrition"]["source"] == "unknown"
    evidence_id = known["evidence_ids"][0]
    assert client.get(f"/api/v1/evidence/{evidence_id}", headers=user).status_code == 403
    assert client.get(f"/api/v1/menu-imports/{job_id}/draft", headers=user).status_code == 403
    updated = {"expected_revision": 1, "currency": "RUB", "items": draft["items"]}
    updated["items"][0]["price_minor"] = 47000
    assert client.put(f"/api/v1/menu-imports/{job_id}/draft", headers=admin, json=updated).status_code == 200
    assert client.put(f"/api/v1/menu-imports/{job_id}/draft", headers=admin, json=updated).status_code == 409
    assert client.post(f"/api/v1/menu-imports/{job_id}/publish", headers=admin, json={"expected_revision": 1}).status_code == 409
    published = client.post(f"/api/v1/menu-imports/{job_id}/publish", headers=admin, json={"expected_revision": 2})
    assert published.status_code == 201, published.text
    assert published.json()["menu"]["version"] == 2
    assert client.put(f"/api/v1/menu-imports/{job_id}/draft", headers=admin, json=updated).status_code == 409
    archived = client.get(f'/api/v1/menu-versions/{old_menu["menu"]["id"]}', headers=user).json()
    assert archived["menu"]["status"] == "archived"
    assert archived["items"] == old_menu["items"]
    assert client.get(f'/api/v1/menu-items/{old_menu["items"][0]["id"]}', headers=user).status_code == 200
    hits = client.post("/api/v1/search", headers=user, json={"query": "dumplings", "constraints": {"restaurant_id": demo_id("restaurant-0")}}).json()["items"]
    assert len(hits) == 2
    assert all(x["item"]["menu_version_id"] == published.json()["menu"]["id"] for x in hits)


def test_foreign_source_evidence_rejected(catalog):
    client, sessions, _, _, admin, _ = catalog
    first, second = upload(client, admin), upload(client, admin)
    process_import(sessions, first)
    process_import(sessions, second)
    draft1 = client.get(f"/api/v1/menu-imports/{first}/draft", headers=admin).json()
    draft2 = client.get(f"/api/v1/menu-imports/{second}/draft", headers=admin).json()
    draft1["items"][0]["evidence_ids"] = draft2["items"][0]["evidence_ids"]
    result = client.put(f"/api/v1/menu-imports/{first}/draft", headers=admin,
        json={"expected_revision": 1, "currency": "RUB", "items": draft1["items"]})
    assert result.status_code == 422


def test_pdf_real_text_extraction_and_unknowns(catalog):
    client, sessions, _, _, admin, _ = catalog
    document = fitz.open()
    page = document.new_page()
    page.insert_text((50, 60), "Chicken rice | 500 RUB | 250 g\nVegetable soup | 300 RUB | 300 g")
    content = document.tobytes()
    document.close()
    job_id = upload(client, admin, content, "pdf", "menu.pdf")
    process_import(sessions, job_id)
    draft = client.get(f"/api/v1/menu-imports/{job_id}/draft", headers=admin).json()
    assert [x["price_minor"] for x in draft["items"]] == [50000, 30000]
    assert all(x["nutrition"]["calories_kcal"] is None for x in draft["items"])
    assert all(x["ingredients"] == [] for x in draft["items"])
    with sessions() as session:
        evidence = session.scalars(select(EvidenceRow).where(EvidenceRow.import_id == job_id)).all()
        assert all(x.page_number == 1 for x in evidence)


def test_file_signature_limits_and_failed_job(catalog):
    client, sessions, _, _, admin, _ = catalog
    response = client.post("/api/v1/menu-imports", headers=admin,
        data={"restaurant_id": demo_id("restaurant-0"), "source_kind": "pdf"}, files={"file": ("fake.pdf", b"not a PDF")})
    assert response.status_code == 422
    # A valid signature alone is not enough: extraction fails and failure is persisted.
    identifier = upload(client, admin, b"%PDF-invalid", "pdf", "broken.pdf")
    process_import(sessions, identifier)
    status = client.get(f"/api/v1/menu-imports/{identifier}", headers=admin).json()
    assert status["status"] == "failed" and status["error"]["code"] == "import_failed"


def test_no_implicit_fake_embedding_provider(monkeypatch):
    monkeypatch.setenv("CATALOG_EMBEDDING_PROVIDER", "test")
    monkeypatch.delenv("CATALOG_TEST_MODE", raising=False)
    with pytest.raises(RuntimeError):
        configured_encoder()
    with pytest.raises(ValueError):
        NutritionFacts(calories_kcal=100, source="unknown")
    with pytest.raises(ValueError):
        NutritionFacts(calories_kcal=100, source="estimated")


def test_worker_recovers_interrupted_persisted_job(catalog):
    client, sessions, encoder, _, admin, _ = catalog
    identifier = upload(client, admin)
    with sessions() as session:
        job = session.get(ImportRow, identifier)
        job.status = "processing"
        job.progress_percent = 40
        session.commit()
    # Recovery is verified without launching an unbounded polling thread.
    worker = CatalogWorker(sessions, encoder)
    worker.thread.start = lambda: None
    worker.start()
    with sessions() as session:
        assert session.get(ImportRow, identifier).status == "queued"
    process_import(sessions, identifier)
    assert client.get(f"/api/v1/menu-imports/{identifier}", headers=admin).json()["status"] == "needs_review"


def test_failed_encoder_leaves_draft_unpublished(catalog):
    from fastapi import HTTPException
    from services.catalog.core import publish
    client, sessions, _, user, admin, _ = catalog
    identifier = upload(client, admin)
    process_import(sessions, identifier)
    before = client.get(f'/api/v1/restaurants/{demo_id("restaurant-0")}/menu', headers=user).json()
    class UnavailableEncoder:
        model_name = "unavailable-real-model"
        def encode(self, _):
            raise RuntimeError("Model offline")
    with sessions() as session, pytest.raises(HTTPException) as failure:
        publish(session, identifier, 1, UnavailableEncoder())
    assert failure.value.status_code == 503
    assert client.get(f"/api/v1/menu-imports/{identifier}", headers=admin).json()["status"] == "needs_review"
    assert client.get(f'/api/v1/restaurants/{demo_id("restaurant-0")}/menu', headers=user).json() == before


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract integration requires executable and rus+eng language data")
def test_image_real_ocr(catalog):
    client, sessions, _, _, admin, _ = catalog
    image = Image.new("RGB", (1500, 300), "white")
    draw = ImageDraw.Draw(image)
    font_path = next((p for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"] if os.path.exists(p)), None)
    font = ImageFont.truetype(font_path, 42) if font_path else ImageFont.load_default(size=42)
    draw.text((30, 60), "Chicken rice | 500 RUB | 250 g", font=font, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    identifier = upload(client, admin, buffer.getvalue(), "image", "menu.png")
    process_import(sessions, identifier)
    draft = client.get(f"/api/v1/menu-imports/{identifier}/draft", headers=admin)
    assert draft.status_code == 200, client.get(f"/api/v1/menu-imports/{identifier}", headers=admin).text
    assert draft.json()["items"][0]["price_minor"] == 50000
    assert draft.json()["items"][0]["nutrition"]["source"] == "unknown"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Scanned-PDF OCR requires real Tesseract rus+eng")
def test_scanned_pdf_real_ocr_fallback(catalog):
    client, sessions, _, _, admin, _ = catalog
    image = Image.new("RGB", (1500, 300), "white")
    draw = ImageDraw.Draw(image)
    font_path = next((p for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"] if os.path.exists(p)), None)
    font = ImageFont.truetype(font_path, 42) if font_path else ImageFont.load_default(size=42)
    draw.text((30, 60), "Vegetable soup | 300 RUB | 250 g", font=font, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    document = fitz.open()
    page = document.new_page(width=750, height=150)
    page.insert_image(page.rect, stream=buffer.getvalue())
    content = document.tobytes()
    document.close()
    identifier = upload(client, admin, content, "pdf", "scan.pdf")
    process_import(sessions, identifier)
    draft = client.get(f"/api/v1/menu-imports/{identifier}/draft", headers=admin)
    assert draft.status_code == 200, client.get(f"/api/v1/menu-imports/{identifier}", headers=admin).text
    assert draft.json()["items"][0]["price_minor"] == 30000
    assert any("OCR" in value for value in draft.json()["validation_warnings"])
