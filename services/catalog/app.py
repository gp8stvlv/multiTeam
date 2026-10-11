from __future__ import annotations

from contextlib import asynccontextmanager
from io import BytesIO
import os
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from services.common.db import make_database
from services.common.errors import install_error_handlers
from services.common.security import Principal, current_user, require_admin

from .core import CatalogWorker, initialize, item_view, menu_view, publish, replace_draft, restaurant_view, semantic_search
from .embeddings import configured_encoder
from .extraction import MAX_BYTES, validate_file
from .models import DishRow, EvidenceRow, ImportRow, MenuItemRow, MenuVersionRow, RestaurantRow, ReviewRow
from .schemas import (Dish, Evidence, MenuDraft, MenuDraftReplace, MenuImport, MenuItem, MenuPublishRequest,
    MenuResponse, Point, Restaurant, RestaurantCreate, RestaurantPage, RestaurantPatch, Review,
    ReviewPage, SearchRequest, SearchResponse)
from .seed import seed_demo


async def remote_profile(authorization):
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(os.getenv("USERS_URL", "http://users:8002") + "/api/v1/me/profile", headers={"Authorization": authorization or ""})
        if response.status_code in (401, 403):
            raise HTTPException(response.status_code, "Profile access denied")
        response.raise_for_status()
        profile = response.json()
        if not isinstance(profile.get("allergies"), list):
            raise ValueError("Profile missing allergies")
        return profile
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "User profile unavailable; mandatory allergies cannot be verified") from exc


def create_app(database=None, encoder=None, profile_loader=None, *, seed=True, start_worker=True):
    engine, session_factory = database or make_database("CATALOG_DATABASE_URL", ".data/catalog.db")
    encoder = encoder or configured_encoder()
    profile_loader = profile_loader or remote_profile
    worker = CatalogWorker(session_factory, encoder)

    @asynccontextmanager
    async def lifespan(application):
        initialize(engine)
        if seed and os.getenv("CATALOG_SEED_DEMO", "true").lower() == "true":
            with session_factory() as session:
                seed_demo(session)
        if start_worker:
            worker.start()
        application.state.engine = engine
        application.state.session_factory = session_factory
        application.state.encoder = encoder
        application.state.worker = worker
        yield
        if start_worker:
            worker.close()

    application = FastAPI(title="ГастроГид: Catalog Service", version="0.1.0", lifespan=lifespan,
        description="Versioned menus, reviewable HTML/PDF/photo imports and real multilingual semantic retrieval. Demo data is explicitly synthetic.")
    install_error_handlers(application)

    def get_session():
        with session_factory() as session:
            yield session

    def get_row(session, model, identifier, name):
        row = session.get(model, str(identifier))
        if row is None:
            raise HTTPException(404, f"{name} not found")
        return row

    @application.get("/health", tags=["Health"])
    def health():
        return {"status": "ok", "service": "catalog"}

    @application.get("/ready", tags=["Health"])
    def ready(response: Response):
        with session_factory() as session:
            session.execute(select(1))
        if start_worker and not worker.ready:
            response.status_code = 503
        return {"status": "ready" if (not start_worker or worker.ready) else "indexing",
            "embedding_model": encoder.model_name, "semantic_search": not start_worker or worker.ready,
            "warning": worker.index_error}

    @application.get("/api/v1/restaurants", response_model=RestaurantPage, tags=["Catalog"])
    def list_restaurants(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
        latitude: float | None = Query(None, ge=-90, le=90), longitude: float | None = Query(None, ge=-180, le=180),
        radius_m: int | None = Query(None, ge=100, le=100000), cuisine: str | None = None,
        sort: str = Query("name", pattern="^(name|rating|distance)$"),
        principal: Principal = Depends(current_user), session=Depends(get_session)):
        if (latitude is None) != (longitude is None):
            raise HTTPException(422, "Latitude and longitude must be supplied together")
        if (radius_m is not None or sort == "distance") and latitude is None:
            raise HTTPException(422, "Radius and distance sorting require coordinates")
        near = Point(latitude=latitude, longitude=longitude) if latitude is not None else None
        rows = session.scalars(select(RestaurantRow).where(RestaurantRow.active.is_(True))).all()
        views = [restaurant_view(session, x, near) for x in rows if cuisine is None or cuisine in x.cuisines]
        if radius_m is not None:
            views = [x for x in views if x.distance_m <= radius_m]
        views.sort(key=lambda x: x.distance_m if sort == "distance" else (-(x.rating or 0) if sort == "rating" else x.name))
        return RestaurantPage(items=views[offset:offset+limit], total=len(views), limit=limit, offset=offset)

    @application.post("/api/v1/restaurants", response_model=Restaurant, status_code=201, tags=["Menu administration"])
    def create_restaurant(data: RestaurantCreate, principal: Principal = Depends(require_admin), session=Depends(get_session)):
        attrs = data.model_dump(exclude={"location"})
        row = RestaurantRow(**attrs, latitude=data.location.latitude, longitude=data.location.longitude)
        session.add(row)
        session.commit()
        return restaurant_view(session, row)

    @application.get("/api/v1/restaurants/{restaurant_id}", response_model=Restaurant, tags=["Catalog"])
    def get_restaurant(restaurant_id: UUID, principal: Principal = Depends(current_user), session=Depends(get_session)):
        return restaurant_view(session, get_row(session, RestaurantRow, restaurant_id, "Restaurant"))

    @application.patch("/api/v1/restaurants/{restaurant_id}", response_model=Restaurant, tags=["Menu administration"])
    def patch_restaurant(restaurant_id: UUID, data: RestaurantPatch, principal: Principal = Depends(require_admin), session=Depends(get_session)):
        row = get_row(session, RestaurantRow, restaurant_id, "Restaurant")
        for key, value in data.model_dump(exclude_unset=True, exclude={"location"}).items():
            setattr(row, key, value)
        if data.location is not None:
            row.latitude, row.longitude = data.location.latitude, data.location.longitude
        session.commit()
        return restaurant_view(session, row)

    @application.get("/api/v1/restaurants/{restaurant_id}/menu", response_model=MenuResponse, tags=["Catalog"])
    def active_menu(restaurant_id: UUID, principal: Principal = Depends(current_user), session=Depends(get_session)):
        row = get_row(session, RestaurantRow, restaurant_id, "Restaurant")
        if not row.active_menu_version_id:
            raise HTTPException(404, "No published menu")
        return menu_view(session, session.get(MenuVersionRow, row.active_menu_version_id))

    @application.get("/api/v1/restaurants/{restaurant_id}/reviews", response_model=ReviewPage, tags=["Catalog"])
    def reviews(restaurant_id: UUID, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
        principal: Principal = Depends(current_user), session=Depends(get_session)):
        get_row(session, RestaurantRow, restaurant_id, "Restaurant")
        statement = select(ReviewRow).where(ReviewRow.restaurant_id == str(restaurant_id))
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = session.scalars(statement.order_by(ReviewRow.reviewed_at.desc()).limit(limit).offset(offset)).all()
        return ReviewPage(items=[Review.model_validate(x) for x in rows], total=total, limit=limit, offset=offset)

    @application.get("/api/v1/dishes/{dish_id}", response_model=Dish, tags=["Catalog"])
    def dish(dish_id: UUID, principal: Principal = Depends(current_user), session=Depends(get_session)):
        return Dish.model_validate(get_row(session, DishRow, dish_id, "Dish"))

    @application.get("/api/v1/menu-items/{menu_item_id}", response_model=MenuItem, tags=["Catalog"])
    def menu_item(menu_item_id: UUID, principal: Principal = Depends(current_user), session=Depends(get_session)):
        return item_view(get_row(session, MenuItemRow, menu_item_id, "Menu item"))

    @application.get("/api/v1/menu-versions/{menu_version_id}", response_model=MenuResponse, tags=["Catalog"])
    def menu_version(menu_version_id: UUID, principal: Principal = Depends(current_user), session=Depends(get_session)):
        return menu_view(session, get_row(session, MenuVersionRow, menu_version_id, "Menu version"))

    def authorized_evidence(session, evidence_id, principal):
        row = get_row(session, EvidenceRow, evidence_id, "Evidence")
        if row.import_id:
            job = session.get(ImportRow, row.import_id)
            if job.status != "published" and principal.role != "admin":
                raise HTTPException(403, "Draft source evidence requires administrator access")
        return row

    @application.get("/api/v1/evidence/{evidence_id}", response_model=Evidence, tags=["Catalog"])
    def evidence(evidence_id: UUID, principal: Principal = Depends(current_user), session=Depends(get_session)):
        return Evidence.model_validate(authorized_evidence(session, evidence_id, principal))

    @application.get("/api/v1/evidence/{evidence_id}/source", tags=["Catalog"], response_class=StreamingResponse,
        responses={200: {"description": "Original uploaded document, downloaded as attachment"}})
    def evidence_source(evidence_id: UUID, principal: Principal = Depends(current_user), session=Depends(get_session)):
        row = authorized_evidence(session, evidence_id, principal)
        if not row.import_id:
            raise HTTPException(404, "This evidence is a text-only source")
        job = session.get(ImportRow, row.import_id)
        # Force download and prohibit browser HTML execution/sniffing.
        return StreamingResponse(BytesIO(job.file_bytes), media_type="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="menu-source.{"html" if job.source_kind == "html" else "pdf" if job.source_kind == "pdf" else "image"}"',
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"})

    @application.post("/api/v1/search", response_model=SearchResponse, tags=["Search"])
    async def search(data: SearchRequest, authorization: str | None = Header(None),
        principal: Principal = Depends(current_user), session=Depends(get_session)):
        if start_worker and not worker.ready:
            raise HTTPException(503, "Semantic index is initializing; retry shortly")
        profile = await profile_loader(authorization)
        allergies = [x["code"] for x in profile.get("allergies", [])]
        data.constraints.excluded_allergen_codes = list(dict.fromkeys(data.constraints.excluded_allergen_codes + allergies))
        return semantic_search(session, data, encoder)

    @application.post("/api/v1/menu-imports", response_model=MenuImport, status_code=202, tags=["Menu administration"])
    async def upload_menu(response: Response, restaurant_id: UUID = Form(...), source_kind: str = Form(...), file: UploadFile = File(...),
        principal: Principal = Depends(require_admin), session=Depends(get_session)):
        get_row(session, RestaurantRow, restaurant_id, "Restaurant")
        content = await file.read(MAX_BYTES + 1)
        await file.close()
        try:
            validate_file(source_kind, content)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        row = ImportRow(restaurant_id=str(restaurant_id), source_kind=source_kind,
            filename=Path(file.filename or "menu").name[:200], content_type=file.content_type or "application/octet-stream", file_bytes=content)
        session.add(row)
        session.commit()
        worker.wake.set()
        response.headers["Location"] = "/api/v1/menu-imports/" + row.id
        response.headers["Retry-After"] = "2"
        return MenuImport.model_validate(row)

    @application.get("/api/v1/menu-imports/{import_id}", response_model=MenuImport, tags=["Menu administration"])
    def get_import(import_id: UUID, principal: Principal = Depends(require_admin), session=Depends(get_session)):
        return MenuImport.model_validate(get_row(session, ImportRow, import_id, "Menu import"))

    def draft_view(row):
        return MenuDraft(import_id=row.id, revision=row.revision, currency=row.currency, items=row.draft_items, validation_warnings=row.warnings)

    @application.get("/api/v1/menu-imports/{import_id}/draft", response_model=MenuDraft, tags=["Menu administration"])
    def get_draft(import_id: UUID, principal: Principal = Depends(require_admin), session=Depends(get_session)):
        row = get_row(session, ImportRow, import_id, "Menu import")
        if row.status != "needs_review":
            raise HTTPException(409, "Draft is not available for review")
        return draft_view(row)

    @application.get("/api/v1/menu-imports/{import_id}/evidence", response_model=list[Evidence], tags=["Menu administration"])
    def import_evidence(import_id: UUID, principal: Principal = Depends(require_admin), session=Depends(get_session)):
        get_row(session, ImportRow, import_id, "Menu import")
        return [Evidence.model_validate(x) for x in session.scalars(select(EvidenceRow).where(EvidenceRow.import_id == str(import_id))).all()]

    @application.put("/api/v1/menu-imports/{import_id}/draft", response_model=MenuDraft, tags=["Menu administration"])
    def edit_draft(import_id: UUID, data: MenuDraftReplace, principal: Principal = Depends(require_admin), session=Depends(get_session)):
        row = session.scalar(select(ImportRow).where(ImportRow.id == str(import_id)).with_for_update())
        if row is None:
            raise HTTPException(404, "Menu import not found")
        replace_draft(session, row, data)
        return draft_view(row)

    @application.post("/api/v1/menu-imports/{import_id}/publish", response_model=MenuResponse, status_code=201, tags=["Menu administration"])
    def publish_menu(import_id: UUID, data: MenuPublishRequest, principal: Principal = Depends(require_admin), session=Depends(get_session)):
        result = publish(session, str(import_id), data.expected_revision, encoder)
        worker.wake.set()
        return result

    return application


app = create_app()
