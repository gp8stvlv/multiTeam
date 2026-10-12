import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select, update, func, text
from sqlalchemy.exc import IntegrityError
from services.common.db import make_database
from services.common.errors import install_error_handlers
from services.common.openapi import install_gateway_security_schema
from services.common.security import Principal, current_user, require_admin, create_access_token, auth_secret
from services.recommendations.models import Base, RequestRow, TraceRow, FeedRow, now
from services.recommendations.schemas import RecommendationCreate, RecommendationRun, RecommendationResult, TraceResponse, FeedPage, FeedItem, FeedPatch
from services.recommendations.graph import RecommendationGraph, PipelineError
from services.recommendations.provider import provider_configured, ProviderNotConfigured

engine, Session = make_database("RECOMMENDATIONS_DATABASE_URL", "data/recommendations.db")
logger = logging.getLogger(__name__)


def digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def serialize(row):
    return RecommendationRun(id=row.id, status=row.status, trigger=row.trigger, created_at=aware(row.created_at),
                             updated_at=aware(row.updated_at), result=row.result, error=row.error)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def owned(session, request_id, user_id):
    row = session.get(RequestRow, str(request_id))
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "Recommendation not found")
    return row


def enqueue(user_id, payload, key, trigger="manual", context=None):
    payload_hash = digest(payload)
    with Session() as session:
        previous = session.scalar(select(RequestRow).where(RequestRow.user_id == user_id, RequestRow.idempotency_key == key))
        if previous:
            if previous.payload_hash != payload_hash:
                raise HTTPException(409, "Idempotency-Key was used with a different request")
            return serialize(previous)
        active = session.scalar(select(func.count()).select_from(RequestRow).where(RequestRow.user_id == user_id, RequestRow.status.in_(["queued", "running"])))
        if active >= 2:
            raise HTTPException(429, "Wait for your existing recommendations to finish")
        row = RequestRow(user_id=user_id, idempotency_key=key, payload_hash=payload_hash, payload=payload,
                         trigger=trigger, context=context)
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            previous = session.scalar(select(RequestRow).where(RequestRow.user_id == user_id, RequestRow.idempotency_key == key))
            if not previous or previous.payload_hash != payload_hash:
                raise HTTPException(409, "Conflicting recommendation request")
            return serialize(previous)
        return serialize(row)


def claim_job():
    lease = str(uuid4())
    with Session() as session:
        cutoff = now() - timedelta(minutes=15)
        # A crashed worker's lease is reclaimed. A heartbeat protects active runs.
        session.execute(update(RequestRow).where(RequestRow.status == "running", RequestRow.updated_at < cutoff,
                                                RequestRow.attempts < 3).values(status="queued", lease_owner=None))
        session.execute(update(RequestRow).where(RequestRow.status == "running", RequestRow.updated_at < cutoff,
                                                RequestRow.attempts >= 3).values(status="failed", lease_owner=None,
            error={"code": "worker_retries_exhausted", "message": "Recommendation worker repeatedly stopped", "request_id": str(uuid4()), "details": {}}, updated_at=now()))
        row = session.scalar(select(RequestRow).where(RequestRow.status == "queued").order_by(RequestRow.created_at).limit(1))
        if row is None:
            session.commit()
            return None
        changed = session.execute(update(RequestRow).where(RequestRow.id == row.id, RequestRow.status == "queued")
                                  .values(status="running", lease_owner=lease, attempts=RequestRow.attempts+1, updated_at=now()))
        session.commit()
        if changed.rowcount != 1:
            return None
        session.refresh(row)
        return row.id, row.user_id, row.payload, row.context, lease


async def heartbeat(request_id, lease):
    while True:
        await asyncio.sleep(30)
        with Session() as session:
            session.execute(update(RequestRow).where(RequestRow.id == request_id, RequestRow.lease_owner == lease,
                                                    RequestRow.status == "running").values(updated_at=now()))
            session.commit()


async def worker():
    while True:
        try:
            job = claim_job()
            if not job:
                await asyncio.sleep(1)
                continue
            request_id, user_id, payload, context, lease = job
            pulse = asyncio.create_task(heartbeat(request_id, lease))
            def trace(record):
                with Session() as session:
                    session.add(TraceRow(request_id=request_id, data=record))
                    session.commit()
            try:
                token = create_access_token(Principal(user_id), lifetime_seconds=1800)
                result = await asyncio.wait_for(RecommendationGraph(token, trace).run(RecommendationCreate.model_validate(payload), context), timeout=600)
                result = RecommendationResult.model_validate(result).model_dump(mode="json")
                with Session() as session:
                    session.execute(update(RequestRow).where(RequestRow.id == request_id, RequestRow.lease_owner == lease)
                                    .values(status="completed", result=result, error=None, updated_at=now(), lease_owner=None))
                    session.commit()
            except asyncio.CancelledError:
                with Session() as session:
                    session.execute(update(RequestRow).where(RequestRow.id == request_id, RequestRow.lease_owner == lease)
                                    .values(status="queued", lease_owner=None, updated_at=now()))
                    session.commit()
                raise
            except Exception as exc:
                if isinstance(exc, PipelineError):
                    code, message = exc.code, exc.message
                elif isinstance(exc, ProviderNotConfigured):
                    code, message = "llm_not_configured", "LLM provider is not configured"
                elif isinstance(exc, TimeoutError):
                    code, message = "recommendation_timeout", "Recommendation exceeded its time budget"
                else:
                    # Provider exceptions can include secret-bearing HTTP headers; never persist str(exc).
                    code, message = "recommendation_failed", "Model or recommendation pipeline failed"
                logger.warning("Recommendation failed id=%s type=%s", request_id, type(exc).__name__)
                with Session() as session:
                    session.execute(update(RequestRow).where(RequestRow.id == request_id, RequestRow.lease_owner == lease)
                                    .values(status="failed", result=None, lease_owner=None, updated_at=now(),
                                            error={"code": code, "message": message, "request_id": request_id, "details": {}}))
                    session.commit()
            finally:
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Worker cycle failed type=%s", type(exc).__name__)
            await asyncio.sleep(5)


async def proactive_cycle():
    if not provider_configured():
        return
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(os.getenv("USERS_URL", "http://localhost:8002") + "/api/v1/internal/users-contexts",
                                    headers={"X-Internal-Token": os.getenv("INTERNAL_TOKEN", "")})
        response.raise_for_status()
        contexts = response.json()["items"]
        for context in contexts:
            profile = context["profile"]
            local = datetime.now(ZoneInfo(profile["timezone"]))
            if not profile["proactive_enabled"] or not 11 <= local.hour <= 20:
                continue
            key = local.date().isoformat()
            with Session() as session:
                if session.scalar(select(FeedRow.id).where(FeedRow.user_id == context["user_id"], FeedRow.daily_key == key)):
                    continue
            summary_response = await client.get(os.getenv("USERS_URL", "http://localhost:8002") + "/api/v1/me/daily-summary",
                                                params={"date": key}, headers={"Authorization": "Bearer " + context["jwt"]})
            summary_response.raise_for_status()
            remaining = summary_response.json()["remaining"].get("calories_kcal")
            if remaining is not None and float(remaining) <= 0:
                continue
            cuisines = ", ".join(profile.get("favorite_cuisines", []))
            constraints = {"budget_minor": profile.get("default_budget_minor"), "currency": profile["currency"]}
            if remaining is not None:
                constraints["max_calories_kcal"] = float(remaining)
            payload = RecommendationCreate(mode="meal_plan", query="Предложи обед или ужин" + (f"; любимые кухни: {cuisines}" if cuisines else ""),
                                           constraints=constraints, max_options=2).model_dump(mode="json")
            try:
                run = enqueue(context["user_id"], payload, "proactive:" + key, "proactive")
                with Session() as session:
                    session.add(FeedRow(user_id=context["user_id"], daily_key=key, recommendation_id=str(run.id),
                                        title="Идеи на сегодня с учётом ваших предпочтений", expires_at=now()+timedelta(hours=24)))
                    session.commit()
            except (HTTPException, IntegrityError):
                continue


async def scheduler():
    while True:
        try:
            await proactive_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Proactive scheduler failed type=%s", type(exc).__name__)
        await asyncio.sleep(int(os.getenv("PROACTIVE_INTERVAL_SECONDS", "900")))


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    tasks = []
    if os.getenv("RECOMMENDATIONS_WORKER_ENABLED", "true").lower() == "true":
        tasks = [asyncio.create_task(worker()), asyncio.create_task(scheduler())]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    engine.dispose()


app = FastAPI(title="ГастроГид — Recommendations", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
install_gateway_security_schema(app)


@app.get("/health")
def health():
    return {"status": "ok", "service": "recommendations", "llm_configured": provider_configured()}


@app.get("/ready")
def ready():
    try:
        auth_secret()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "Database or authentication configuration is unavailable")
    if not provider_configured():
        raise HTTPException(503, {"code": "llm_not_configured", "message": "Configure the GLM or OpenRouter provider"})
    return {"status": "ready", "service": "recommendations"}


@app.post("/api/v1/recommendations", response_model=RecommendationRun, status_code=202, tags=["Рекомендации"])
def create_recommendation(body: RecommendationCreate, response: Response, principal: Principal = Depends(current_user),
                          idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128)):
    context = None
    with Session() as session:
        if body.context_request_id:
            parent = owned(session, body.context_request_id, principal.id)
            context = {"query": parent.payload["query"], "result": parent.result}
        existing = session.scalar(select(RequestRow).where(RequestRow.user_id == principal.id, RequestRow.idempotency_key == idempotency_key))
        if not existing and not provider_configured():
            raise HTTPException(503, {"code": "llm_not_configured", "message": "Configure the GLM or OpenRouter provider"})
    run = enqueue(principal.id, body.model_dump(mode="json"), idempotency_key, context=context)
    response.headers["Location"] = f"/api/v1/recommendations/{run.id}"
    response.headers["Retry-After"] = "2"
    return run


@app.get("/api/v1/recommendations/{recommendation_id}", response_model=RecommendationRun, tags=["Рекомендации"])
def get_recommendation(recommendation_id: UUID, response: Response, principal: Principal = Depends(current_user)):
    with Session() as session:
        row = owned(session, recommendation_id, principal.id)
        if row.status in ("queued", "running"):
            response.headers["Retry-After"] = "2"
        return serialize(row)


@app.get("/api/v1/recommendations/{recommendation_id}/trace", response_model=TraceResponse, tags=["Администрирование"])
def get_trace(recommendation_id: UUID, principal: Principal = Depends(require_admin)):
    with Session() as session:
        if not session.get(RequestRow, str(recommendation_id)):
            raise HTTPException(404, "Recommendation not found")
        rows = session.scalars(select(TraceRow).where(TraceRow.request_id == str(recommendation_id)))
        return {"recommendation_id": recommendation_id, "runs": [r.data for r in rows]}


@app.get("/api/v1/me/feed", response_model=FeedPage, tags=["Лента"])
def get_feed(principal: Principal = Depends(current_user), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    with Session() as session:
        query = select(FeedRow).join(RequestRow, RequestRow.id == FeedRow.recommendation_id).where(
            FeedRow.user_id == principal.id, FeedRow.expires_at > now(), RequestRow.status == "completed", FeedRow.status != "dismissed")
        # Feed contains actionable suggestions; empty/clarification results stay in run history.
        ready_rows = [r for r in session.scalars(query.order_by(FeedRow.created_at.desc())).all()
                      if session.get(RequestRow, r.recommendation_id).result.get("outcome") == "ready"]
        return {"items": [feed_item(r) for r in ready_rows[offset:offset+limit]],
                "total": len(ready_rows), "limit": limit, "offset": offset}


def feed_item(row):
    return FeedItem(id=row.id, recommendation_id=row.recommendation_id, title=row.title, status=row.status,
                    created_at=aware(row.created_at), expires_at=aware(row.expires_at))


@app.patch("/api/v1/me/feed/{feed_item_id}", response_model=FeedItem, tags=["Лента"])
def patch_feed(feed_item_id: UUID, body: FeedPatch, principal: Principal = Depends(current_user)):
    with Session() as session:
        row = session.get(FeedRow, str(feed_item_id))
        if not row or row.user_id != principal.id:
            raise HTTPException(404, "Feed item not found")
        row.status = body.status
        session.commit()
        return feed_item(row)
