# MVP implementation contract

Scope: three persistent FastAPI services; real semantic retrieval over versioned restaurant menus and evidence; LangGraph multi-agent recommendations; diary and nutrition; basic native SwiftUI iOS client. Existing design-openapi.yaml defines the intended external shapes. Runtime OpenAPI is generated from the implemented models.

## Shared conventions
- Repository root is on PYTHONPATH. Entrypoints: `services.catalog.app:app`, `services.users.app:app`, `services.recommendations.app:app`.
- All external endpoints use `/api/v1`; health endpoints `/health` and `/ready`.
- Authentication: common `current_user` dependency returns `Principal(id: str, role: str)`. JWT `sub` is user UUID. `require_admin` checks admin role. Agents use a delegated user JWT for reads. Internal endpoints require `X-Internal-Token` via `require_internal`.
- Shared modules are owned by the main agent: `services/common/security.py`, `errors.py`, `db.py`. Imports can be added before these modules exist.
- Errors: `install_error_handlers(app)` standardizes HTTP/validation failures to `{code,message,request_id,details}`. Raise FastAPI HTTPException for business errors.
- Database helper: `make_database(env_name, fallback_path)` returns `(engine, SessionLocal)` using SQLAlchemy 2. Use your own DeclarativeBase and `Base.metadata.create_all(engine)` in lifespan. Separate DB ownership. Fallback local SQLite exists for isolated tests; deployment uses PostgreSQL.
- Environment names: `CATALOG_DATABASE_URL`, `USERS_DATABASE_URL`, `RECOMMENDATIONS_DATABASE_URL`; HTTP URLs `CATALOG_URL=http://catalog:8001`, `USERS_URL=http://users:8002`, `RECOMMENDATIONS_URL=http://recommendations:8003`.
- AUTH_SECRET shared across backend; INTERNAL_TOKEN shared for server-only calls. No secrets in Git. Root will create dependency manifest and Compose.
- PostgreSQL stores actual embeddings via pgvector, local SQLite can persist JSON vectors for tests. Search must be actual model-derived semantic retrieval in configured production mode, with a clearly labelled test encoder only for isolated automated tests. Never silently replace semantic retrieval with keyword matching.
- Money: integer minor units (RUB kopecks), nutrition per standard portion, null means unknown. Order quantities are integers; consumed portions may be fractional.

## Integration routes
- Catalog: `POST /api/v1/search {query,constraints,limit}` returns `{items:[{item:MenuItem,restaurant:Restaurant,relevance_score,evidence_ids,warnings}],warnings}`. Search filters current version only and includes all evidence IDs.
- Catalog: `GET /api/v1/menu-items/{id}`, `GET /api/v1/evidence/{id}`, restaurant/menu/reviews routes from design. `GET /api/v1/restaurants` paginated.
- Users: `GET /api/v1/me/profile`, `GET /api/v1/me/daily-summary?date=YYYY-MM-DD`, diary CRUD and feedback. `POST /api/v1/nutrition/calculate {items:[{menu_item_id,portions}]}` queries Catalog with caller JWT.
- Users: `GET /api/v1/internal/users-contexts` with internal token returns opted-in user contexts: `{items:[{user_id,profile,jwt}]}`; service-issued short-lived delegated JWT, no credentials. Needed by scheduled proactive recommendations. Each context includes all profile allergies.
- Users: `GET /api/v1/me/feedback` returns user's latest dish feedback for ranking.
- Recommendations: create `POST /api/v1/recommendations {mode,query,constraints,max_options,context_request_id?}` with Idempotency-Key -> 202 RecommendationRun; poll `GET /api/v1/recommendations/{id}`. Run contains optional result and error, owned by authenticated user. Trace admin-only. Feed get/patch. All result numbers and evidence IDs validated against service data, not invented by LLM.
- Recipe/allergen constraints: mandatory exclusions with incomplete data are not treated as verified suitable. Negative evidence requires explicit declarations. Profile allergies cannot be removed by request. Unknown nutrition never counts as zero.
- Uploaded HTML/PDF/images: bounded files, asynchronous import, review/edit draft before publish, immutable menu versions. Source evidence remains available. Images use OCR; PDF first text then OCR fallback. Extracted unknown values remain null. No invented allergens/macros.

## Team ownership
- Catalog agent: only `services/catalog/**`, `tests/test_catalog*.py`, optional catalog fixtures. Own SQLAlchemy models, OCR/import pipeline, vector embeddings/search, seed restaurants and clearly marked demonstration reviews. Tell root additional dependencies and finalized interfaces.
- Users agent: only `services/users/**`, `tests/test_users*.py`. Own account JWT/password handling, profile, diary snapshots/idempotency, exact Decimal nutrition arithmetic, internal proactive contexts. Tell root dependencies/interfaces.
- iOS agent: only `ios/**`. Minimal real SwiftUI UI and URLSession calls against agreed external routes. Provide an Xcode project and portable Foundation-only Swift package/tests if full Xcode unavailable. No credentials or hardcoded tokens.
- Root: shared modules, requirements, agent graph, recommendation persistence/service, gateway/Compose, cross-service tests, deployment, root README. Root will integrate and may adjust shared models/contracts.

## Verification expectations
Each backend owner adds focused meaningful tests for invariants, auth, evidence or arithmetic. Runtime APIs expose complete Pydantic request/response schemas. Demo fixtures have real consistent numbers for examples only, never represented as restaurant measurements. Production embedding/LLM execution is verified separately from deterministic test fixtures.
