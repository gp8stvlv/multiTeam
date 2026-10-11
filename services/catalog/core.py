from __future__ import annotations

import math
import logging
import threading
from uuid import uuid4

from fastapi import HTTPException
from pgvector.sqlalchemy import Vector
from sqlalchemy import cast, delete, func, select, text

from .embeddings import cosine, menu_document
from .extraction import extract
from .models import Base, DishRow, EvidenceRow, ImportRow, MenuItemRow, MenuVersionRow, RestaurantRow, ReviewRow, now
from .schemas import Constraints, DraftItem, MenuDraftReplace, MenuItem, MenuResponse, MenuVersion, Restaurant, SearchHit, SearchResponse
from .retrieval import ranking, semantic_query

logger = logging.getLogger(__name__)


def initialize(engine):
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


def distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p2-p1, math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0, 1-a)))


def restaurant_view(session, row, near=None):
    rating, count = session.execute(select(func.avg(ReviewRow.rating), func.count(ReviewRow.id)).where(ReviewRow.restaurant_id == row.id)).one()
    return Restaurant(id=row.id, name=row.name, address=row.address,
        location={"latitude": row.latitude, "longitude": row.longitude}, cuisines=row.cuisines,
        phone=row.phone, website=row.website, timezone=row.timezone, active=row.active,
        rating=round(float(rating), 2) if rating is not None else None, review_count=count,
        distance_m=round(distance_m(near.latitude, near.longitude, row.latitude, row.longitude), 1) if near else None,
        active_menu_version_id=row.active_menu_version_id, updated_at=row.updated_at, is_demo=row.is_demo)


def item_view(row):
    return MenuItem.model_validate(row)


def menu_view(session, menu):
    items = session.scalars(select(MenuItemRow).where(MenuItemRow.menu_version_id == menu.id).order_by(MenuItemRow.name)).all()
    return MenuResponse(menu=MenuVersion.model_validate(menu), items=[item_view(x) for x in items])


def import_warnings(items):
    warnings = []
    for item in items:
        if item["price_minor"] is None:
            warnings.append(f'{item["name"]}: цена не извлечена; укажите цену до публикации.')
        if item["nutrition"]["source"] == "unknown":
            warnings.append(f'{item["name"]}: КБЖУ неизвестны.')
        if item["allergen_info_status"] != "complete":
            warnings.append(f'{item["name"]}: сведения об аллергенах неполные.')
    return warnings


def process_import(session_factory, import_id):
    # Status and input persist across process restarts. Expensive extraction outside transaction.
    with session_factory() as session:
        job = session.get(ImportRow, import_id)
        if job is None or job.status != "queued":
            return
        job.status, job.progress_percent = "processing", 10
        job.attempts += 1
        session.commit()
        kind, content = job.source_kind, job.file_bytes
    try:
        result = extract(kind, content)
        with session_factory() as session:
            job = session.get(ImportRow, import_id)
            if job.status != "processing":
                return
            session.execute(delete(EvidenceRow).where(EvidenceRow.import_id == import_id, EvidenceRow.menu_item_id.is_(None)))
            mapping = {}
            for fragment in result.fragments:
                identifier = str(uuid4())
                mapping[fragment.key] = identifier
                session.add(EvidenceRow(id=identifier, restaurant_id=job.restaurant_id, import_id=job.id,
                    source_kind=job.source_kind, source_reference=job.filename,
                    page_number=fragment.page, fragment_text=fragment.text))
            items = []
            for item in result.items:
                evidence_id = mapping[item["draft_key"]]
                item["evidence_ids"] = [evidence_id]
                for declaration in item["allergens"]:
                    declaration["evidence_id"] = evidence_id
                if item["nutrition"]["source"] == "restaurant_declared":
                    item["nutrition"]["evidence_ids"] = [evidence_id]
                items.append(DraftItem.model_validate(item).model_dump(mode="json"))
            job.draft_items, job.warnings = items, result.warnings + import_warnings(items)
            job.status, job.progress_percent, job.revision, job.error = "needs_review", 100, 1, None
            session.commit()
    except Exception as exc:
        logger.exception("Menu import failed id=%s", import_id)
        with session_factory() as session:
            job = session.get(ImportRow, import_id)
            job.status, job.progress_percent = "failed", 100
            # Do not expose stack traces, filesystem paths or environment values.
            message = str(exc) if isinstance(exc, ValueError) else "Document processing failed; check server logs"
            job.error = {"code": "import_failed", "message": message, "request_id": None, "details": None}
            session.commit()


def validate_evidence(session, job, items):
    for item in items:
        if item.existing_dish_id:
            dish = session.get(DishRow, str(item.existing_dish_id))
            if dish is None or dish.restaurant_id != job.restaurant_id:
                raise HTTPException(422, "existing_dish_id must belong to this restaurant")
        ids = set(str(x) for x in item.evidence_ids + item.nutrition.evidence_ids)
        ids.update(str(x.evidence_id) for x in item.allergens if x.evidence_id)
        if not item.evidence_ids:
            raise HTTPException(422, "Each draft item must reference uploaded source evidence")
        for identifier in ids:
            evidence = session.get(EvidenceRow, identifier)
            if evidence is None or evidence.import_id != job.id or evidence.restaurant_id != job.restaurant_id:
                raise HTTPException(422, "Evidence must belong to this menu import")
        for declaration in item.allergens:
            if not declaration.evidence_id:
                raise HTTPException(422, "Allergen declarations require source evidence")


def replace_draft(session, job, data: MenuDraftReplace):
    if job.status != "needs_review":
        raise HTTPException(409, "Only a reviewable draft can be edited")
    if job.revision != data.expected_revision:
        raise HTTPException(409, "Draft revision has changed; reload before editing")
    validate_evidence(session, job, data.items)
    job.draft_items = [x.model_dump(mode="json") for x in data.items]
    job.currency, job.revision = data.currency, job.revision + 1
    job.warnings = ["Черновик отредактирован администратором; сверьте данные с оригиналом."] + import_warnings(job.draft_items)
    session.commit()


def publish(session, import_id, expected_revision, encoder):
    job = session.scalar(select(ImportRow).where(ImportRow.id == import_id).with_for_update())
    if job is None:
        raise HTTPException(404, "Menu import not found")
    if job.status != "needs_review":
        raise HTTPException(409, "Menu import is not ready for publication")
    if job.revision != expected_revision:
        raise HTTPException(409, "Draft revision has changed")
    items = [DraftItem.model_validate(x) for x in job.draft_items]
    if not items or any(x.price_minor is None for x in items):
        raise HTTPException(422, "A published menu requires at least one item and all prices")
    validate_evidence(session, job, items)
    existing_ids = [str(x.existing_dish_id) for x in items if x.existing_dish_id]
    if len(existing_ids) != len(set(existing_ids)):
        raise HTTPException(422, "A dish may appear only once in a menu version")
    # Compute real vectors BEFORE making any published data visible.
    try:
        vectors = encoder.encode([" ".join([x.name, x.description or "", " ".join(x.ingredients), " ".join(x.tags)]) for x in items])
    except Exception as exc:
        logger.exception("Embedding unavailable during publish")
        raise HTTPException(503, "Semantic encoder unavailable; draft remains unchanged") from exc
    restaurant = session.scalar(select(RestaurantRow).where(RestaurantRow.id == job.restaurant_id).with_for_update())
    version_number = (session.scalar(select(func.max(MenuVersionRow.version)).where(MenuVersionRow.restaurant_id == job.restaurant_id)) or 0) + 1
    menu_id = str(uuid4())
    if restaurant.active_menu_version_id:
        session.get(MenuVersionRow, restaurant.active_menu_version_id).status = "archived"
    menu = MenuVersionRow(id=menu_id, restaurant_id=job.restaurant_id, import_id=job.id,
        version=version_number, currency=job.currency, status="published")
    session.add(menu)
    session.flush()
    for draft, vector in zip(items, vectors):
        dish_id = str(draft.existing_dish_id) if draft.existing_dish_id else str(uuid4())
        if draft.existing_dish_id is None:
            session.add(DishRow(id=dish_id, restaurant_id=job.restaurant_id, name=draft.name, description=draft.description))
            session.flush()
        item_id = str(uuid4())
        ids = set(str(x) for x in draft.evidence_ids + draft.nutrition.evidence_ids)
        ids.update(str(x.evidence_id) for x in draft.allergens if x.evidence_id)
        mapping = {identifier: str(uuid4()) for identifier in ids}
        nutrition = draft.nutrition.model_dump(mode="json")
        nutrition["evidence_ids"] = [mapping[x] for x in nutrition["evidence_ids"]]
        declarations = [x.model_dump(mode="json") for x in draft.allergens]
        for declaration in declarations:
            if declaration["evidence_id"]:
                declaration["evidence_id"] = mapping[declaration["evidence_id"]]
        row = MenuItemRow(id=item_id, dish_id=dish_id, restaurant_id=job.restaurant_id,
            menu_version_id=menu_id, name=draft.name, description=draft.description,
            ingredients=draft.ingredients, ingredients_complete=draft.ingredients_complete,
            tags=draft.tags, price_minor=draft.price_minor, currency=job.currency,
            portion_weight_g=draft.portion_weight_g, allergen_info_status=draft.allergen_info_status,
            allergens=declarations, nutrition=nutrition, evidence_ids=[mapping[str(x)] for x in draft.evidence_ids],
            embedding=vector, embedding_model=encoder.model_name)
        session.add(row)
        session.flush()
        for old_id, new_id in mapping.items():
            source = session.get(EvidenceRow, old_id)
            session.add(EvidenceRow(id=new_id, restaurant_id=job.restaurant_id, import_id=job.id,
                menu_item_id=item_id, source_kind=source.source_kind, source_reference=source.source_reference,
                page_number=source.page_number, fragment_text=source.fragment_text, is_demo=source.is_demo))
    restaurant.active_menu_version_id, restaurant.updated_at = menu_id, now()
    job.status, job.published_menu_version_id = "published", menu_id
    session.commit()
    return menu_view(session, menu)


def filter_item(item, restaurant, constraints: Constraints):
    if constraints.restaurant_id and restaurant.id != str(constraints.restaurant_id):
        return False
    if constraints.currency != item.currency:
        return False
    if constraints.budget_minor is not None and item.price_minor > constraints.budget_minor:
        return False
    if constraints.near:
        p = constraints.near.point
        if distance_m(p.latitude, p.longitude, restaurant.latitude, restaurant.longitude) > constraints.near.radius_m:
            return False
    nutrition = item.nutrition
    if constraints.max_calories_kcal is not None:
        kcal = nutrition.get("calories_kcal")
        if kcal is None or kcal > constraints.max_calories_kcal:
            return False
    if constraints.min_protein_g is not None:
        protein = nutrition.get("protein_g")
        if protein is None or protein < constraints.min_protein_g:
            return False
    for code in constraints.excluded_allergen_codes:
        declarations = [x for x in item.allergens if x["code"] == code]
        if not declarations or any(x["status"] != "declared_absent" or not x.get("evidence_id") for x in declarations):
            return False
    if constraints.excluded_ingredients:
        if not item.ingredients_complete:
            return False
        ingredients_text = " ".join(item.ingredients).casefold()
        if any(value.casefold() in ingredients_text for value in constraints.excluded_ingredients):
            return False
    if constraints.spice_preference == "none":
        if "not_spicy" not in item.tags or "spicy" in item.tags or "mild" in item.tags:
            return False
    elif constraints.spice_preference == "mild":
        if "spicy" in item.tags or not ("mild" in item.tags or "not_spicy" in item.tags):
            return False
    return True


def semantic_search(session, data, encoder):
    try:
        query_vector = encoder.encode([semantic_query(data.query)])[0]
    except Exception as exc:
        logger.exception("Semantic encoding failed")
        raise HTTPException(503, "Semantic encoder unavailable; search was not substituted") from exc
    statement = select(MenuItemRow, RestaurantRow).join(RestaurantRow, RestaurantRow.id == MenuItemRow.restaurant_id).where(
        RestaurantRow.active.is_(True), MenuItemRow.menu_version_id == RestaurantRow.active_menu_version_id,
        MenuItemRow.embedding.is_not(None), MenuItemRow.embedding_model == encoder.model_name)
    if data.constraints.restaurant_id:
        statement = statement.where(RestaurantRow.id == str(data.constraints.restaurant_id))
    if data.constraints.budget_minor is not None:
        statement = statement.where(MenuItemRow.price_minor <= data.constraints.budget_minor)
    statement = statement.where(MenuItemRow.currency == data.constraints.currency)
    if session.bind.dialect.name == "postgresql":
        # PostgreSQL executes actual vector cosine distance; SQLite computes same metric in Python.
        statement = statement.order_by(cast(MenuItemRow.embedding, Vector(384)).cosine_distance(query_vector))
    rows = list(session.execute(statement).all())
    if session.bind.dialect.name != "postgresql":
        rows.sort(key=lambda pair: cosine(pair[0].embedding, query_vector), reverse=True)
    # All candidates have real model vectors. SQL still executes cosine ordering;
    # explicit food-name/tag matches are a separately reported reranking signal.
    # This is deliberately after vector retrieval, never an encoder-failure path.
    ranked = []
    for item, restaurant in rows:
        if filter_item(item, restaurant, data.constraints):
            similarity = max(-1.0, min(1.0, cosine(item.embedding, query_vector)))
            score, matched_terms = ranking(data.query, item, similarity)
            ranked.append((score, similarity, matched_terms, item, restaurant))
    ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)
    hits = []
    for ranking_score, similarity, matched_terms, item, restaurant in ranked[:data.limit]:
        evidence_ids = list(item.evidence_ids)
        reviews = session.scalars(select(ReviewRow).where(ReviewRow.restaurant_id == restaurant.id,
            ReviewRow.embedding_model == encoder.model_name, ReviewRow.embedding.is_not(None))).all()
        reviews.sort(key=lambda x: cosine(x.embedding, query_vector), reverse=True)
        evidence_ids += [x.evidence_id for x in reviews[:2] if cosine(x.embedding, query_vector) > 0.15]
        warnings = []
        if item.is_demo:
            warnings.append("Демонстрационные данные: не меню реального ресторана.")
        if item.nutrition["source"] == "estimated":
            warnings.append("КБЖУ приблизительные; см. метод и источник.")
        if item.nutrition["source"] == "unknown":
            warnings.append("Пищевая ценность неизвестна.")
        if item.allergen_info_status != "complete":
            warnings.append("Сведения об аллергенах неполные.")
        hits.append(SearchHit(item=item_view(item), restaurant=restaurant_view(session, restaurant, data.constraints.near.point if data.constraints.near else None),
            relevance_score=round(max(0, similarity), 6), cosine_similarity=round(similarity, 6),
            ranking_score=round(ranking_score, 6), matched_query_terms=matched_terms,
            evidence_ids=list(dict.fromkeys(evidence_ids)), warnings=warnings))
    missing = session.scalar(select(func.count(MenuItemRow.id)).join(RestaurantRow, RestaurantRow.id == MenuItemRow.restaurant_id).where(
        MenuItemRow.menu_version_id == RestaurantRow.active_menu_version_id, RestaurantRow.active.is_(True),
        (MenuItemRow.embedding.is_(None)) | (MenuItemRow.embedding_model.is_(None)) | (MenuItemRow.embedding_model != encoder.model_name)))
    warnings = ["Часть меню ещё индексируется; поиск может быть неполным."] if missing else []
    return SearchResponse(items=hits, warnings=warnings, embedding_model=encoder.model_name)


def index_pending(session_factory, encoder, *, force=False):
    with session_factory() as session:
        for model in [MenuItemRow, ReviewRow]:
            statement = select(model)
            if not force:
                statement = statement.where((model.embedding.is_(None)) | (model.embedding_model.is_(None)) | (model.embedding_model != encoder.model_name))
            rows = session.scalars(statement).all()
            for start in range(0, len(rows), 32):
                batch = rows[start:start+32]
                texts = [menu_document(x) if isinstance(x, MenuItemRow) else x.text for x in batch]
                vectors = encoder.encode(texts)
                for row, vector in zip(batch, vectors):
                    row.embedding, row.embedding_model = vector, encoder.model_name
                session.commit()


class CatalogWorker:
    def __init__(self, session_factory, encoder):
        self.session_factory, self.encoder = session_factory, encoder
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.ready = False
        self.index_error = None
        self.refresh_index = True
        self.thread = threading.Thread(target=self.run, name="catalog-worker", daemon=True)

    def start(self):
        with self.session_factory() as session:
            for row in session.scalars(select(ImportRow).where(ImportRow.status == "processing")):
                row.status = "queued"
                row.progress_percent = 0
            session.commit()
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            # Import processing is independent of model availability.
            try:
                with self.session_factory() as session:
                    ids = list(session.scalars(select(ImportRow.id).where(ImportRow.status == "queued").order_by(ImportRow.created_at)))
                for identifier in ids:
                    if self.stop.is_set():
                        return
                    process_import(self.session_factory, identifier)
                index_pending(self.session_factory, self.encoder, force=self.refresh_index)
                self.refresh_index = False
                self.ready, self.index_error = True, None
            except Exception:
                self.ready, self.index_error = False, "Semantic index unavailable"
                logger.exception("Catalog processing/indexing failed; retrying without fallback")
            self.wake.wait(5 if self.ready else 30)
            self.wake.clear()

    def close(self):
        self.stop.set()
        self.wake.set()
        self.thread.join(timeout=5)
