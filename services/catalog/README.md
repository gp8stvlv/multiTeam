# Catalog Service

Persistent FastAPI catalog for GastroGuide. Owns restaurants, stable dishes,
immutable published menu versions, reviewable imports and source evidence.

## Runtime

Run from the repository root:

```sh
uvicorn services.catalog.app:app --host 0.0.0.0 --port 8001 --workers 1
```

The service contains one durable polling worker. Uploads persist their bytes and
status in the database before returning `202`; `processing` jobs are recovered
after restart. Use one application process/replica for this MVP. OCR subprocesses
have a timeout. Uploads are limited to 20 MiB, 30 PDF pages and 25 megapixels.

Configuration:

| Variable | Purpose |
| --- | --- |
| `CATALOG_DATABASE_URL` | PostgreSQL connection; SQLite fallback `.data/catalog.db` is local development only |
| `USERS_URL` | Users service origin; `/search` retrieves authenticated profile allergies |
| `AUTH_SECRET` | Same JWT signing secret as other services |
| `CATALOG_SEED_DEMO` | `true` by default; inserts five synthetic restaurants and 60 dishes once |
| `CATALOG_MODEL_CACHE` | Writable persistent model cache; default `/tmp/gastrogid-models` |
| `CATALOG_EMBEDDING_THREADS` | ONNX CPU threads, default `2` |
| `CATALOG_EMBEDDING_MODEL` | Supported 384-dimensional model; default below |

`/health` is liveness, `/ready` remains `503` while the initial index/model is
unavailable. `/search` fails rather than silently replacing semantic retrieval.
Published items receive embeddings before publication commits. PostgreSQL stores
real `vector(384)` and performs cosine-distance ordering using pgvector; SQLite
stores JSON vectors for development and isolated tests. Model names are persisted
with embeddings; changing the model triggers reindexing.

The default model is
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, a multilingual
384-dimensional ONNX model supported by FastEmbed, Apache 2.0.
[Official supported model list](https://qdrant.github.io/fastembed/examples/Supported_Models/).
The first run downloads model artifacts. Persist the model cache for deployments.
The nonsemantic token-hash encoder is explicit test infrastructure only:
`CATALOG_EMBEDDING_PROVIDER=test` requires `CATALOG_TEST_MODE=1` and is rejected
when `APP_ENV=production`. No encoder fallback exists.

Dependencies: FastAPI, SQLAlchemy 2, pgvector, FastEmbed, BeautifulSoup, PyMuPDF,
Pillow, httpx and python-multipart. Images and scanned PDFs require the
`tesseract` executable with `rus` and `eng` language packages.

## Import workflow

1. Admin uploads bytes to `POST /api/v1/menu-imports` (`restaurant_id`,
   `source_kind=html|pdf|image`, `file`). URLs are never fetched.
2. Poll `GET /api/v1/menu-imports/{id}`. `needs_review` indicates extracted draft.
3. Inspect `/draft` and `/evidence`. Evidence includes original text and page
   numbers; `/api/v1/evidence/{id}/source` downloads the uploaded original.
4. Replace `/draft` with `PUT`, specifying `expected_revision`. Stale revisions
   return `409`. Every item and declared allergen needs evidence from this import.
5. `POST /publish` with the current revision. Missing prices/empty drafts are
   rejected. Publication archives the previous version; historical item IDs,
   prices, nutrition and evidence remain accessible unchanged.

HTML extraction supports `.menu-item`, `[data-menu-item]`, Schema.org MenuItem,
ordinary tables and price-bearing lines. For explicit machine-readable menus:

```html
<div class="menu-item" data-name="Хинкали"
     data-price="750" data-weight-g="360"
     data-ingredients="пшеничная мука,говядина,лук"
     data-ingredients-complete="true"
     data-tags="georgian,main,mild"
     data-allergens="wheat:contains;peanut:declared_absent"
     data-allergen-status="partial"
     data-nutrition-basis="per_portion" data-kcal="620"
     data-protein-g="32" data-fat-g="22" data-carbs-g="73.5">
  Хинкали | 750 ₽ | 360 г
</div>
```

`data-price` is major RUB units; `data-price-minor` is integer kopecks.
PDF text and OCR support `Chicken rice | 500 RUB | 250 g` or
`Хинкали | 750 ₽ | 360 г | состав: мука,говядина`.
Text nutrition is extracted only from explicit per-portion labels, e.g.
`на порцию | ккал: 620 | белки: 32 | жиры: 22 | углеводы: 73.5`.
Nutrition per 100 g is not mislabeled as per portion. Unrecognized arbitrary
layouts preserve source fragments for manual review. No ingredients, allergens
or nutrition are generated from dish names.

## Retrieval invariants

Search is restricted to active restaurants and their current published menu.
Budget, radius and nutrition constraints are mandatory; unknown nutrition fails
the relevant mandatory condition. Profile allergies are merged server-side with
request exclusions. Missing/unavailable profile is a `503`, never a bypass.

Excluded allergens require explicit `declared_absent` declarations with source
evidence. Missing declarations, `contains` and `may_contain` fail the filter.
Mandatory ingredient exclusions require `ingredients_complete=true` and no
matching ingredient. This boolean is an extension to the initial design so
incomplete composition cannot masquerade as a complete list.

Spice constraints require explicit consistent tags: `none` admits only
`not_spicy` without `mild`/`spicy`; `mild` admits `mild` or `not_spicy` but rejects
`spicy`; unknown spice information satisfies neither mandatory constraint.

Short dish requests use transparent hybrid ranking: real semantic candidates are
reranked with an explicit bonus for name/tag query-token matches (including a
small Russian inflection normalization). Conversational lead-ins such as
`хочу` are removed from the embedding query. Synthetic metadata disclaimers stay
visible in responses but are excluded from food embeddings. The model must still
be available and items indexed; no keyword fallback exists.

`cosine_similarity` reports actual cosine in [-1, 1]; `relevance_score` retains
the existing nonnegative cosine API field. `ranking_score`,
`matched_query_terms` and `ranking_method` explain the separate hybrid ordering.
PostgreSQL performs vector cosine ordering before application reranking. The
small MVP index is refreshed on service startup so changed document construction
cannot leave stale vectors under the same model name.

Hits return item/menu IDs, numeric cosine similarity, item evidence and relevant
restaurant-review evidence. Review evidence is used for explanation only, not
for proving nutritional/allergen facts. All seeded prices, nutrition, restaurant
locations and reviews are marked as synthetic fixtures; demonstration nutrition
is `estimated` with an explicit method, not real restaurant declarations.

## Checks

```sh
python -m pytest -q tests/test_catalog.py

# Optional actual multilingual ONNX inference, after downloading model weights:
CATALOG_RUN_MODEL_TESTS=1 CATALOG_MODEL_CACHE=data/model-cache \
  python -m pytest -q tests/test_catalog.py -k real_multilingual_model
```

Tests verify authentication/roles, source authorization, real PDF extraction,
unknown values, publish/revision invariants, historical immutability, mandatory
allergy filtering, cross-import evidence rejection, persistent failures and
model-provider safety. The OCR integration test executes real Tesseract when
installed. Test retrieval uses the explicit test encoder; real model inference
and PostgreSQL vector retrieval must additionally be verified in deployment.
