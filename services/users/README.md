# User & Nutrition service

Entrypoint: `uvicorn services.users.app:app --port 8002` from repository root.

The service owns users, profiles, allergies, diary entries, immutable snapshots and latest dish feedback. SQLAlchemy uses `USERS_DATABASE_URL`; without it, `DATA_DIR/users.db` (default `work/data`) is used for local development. Production uses PostgreSQL.

Passwords are Argon2 hashes via `pwdlib[argon2]`; shared JWT authentication is implemented in `services.common.security`. No admin is created unless `DEMO_ADMIN_PASSWORD` is explicitly configured. Admin email defaults to `admin@example.test`. This seed never changes an existing user's role or password.

External routes match the project contract under `/api/v1`: registration/login, profile GET/PATCH, nutrition calculation, diary GET/POST/PATCH/DELETE, daily summary and feedback GET/POST. Catalog reads forward the authenticated user's bearer token. Feedback verifies dish existence and optional recommendation ownership through the owning services.

Diary POST requires an `Idempotency-Key`. Replays with equal canonical input return the same entry; changed input or a deleted operation returns 409. Keys are scoped to the user and kept after logical deletion. Changing a portion/time/note never refreshes a snapshot from a changed menu.

Nutrition values are per standard portion. Arithmetic uses Decimal, and exact original values are stored as decimal strings in snapshot JSON. API values are JSON numbers. Unknown individual nutrients remain null in totals; `known_subtotals` and `complete_fields` explain partial coverage. An empty day has zero totals. Remaining values are null unless the target and entire total are known. Price calculations sum exact products then round the meal total to integer minor units with ROUND_HALF_UP.

Days use the profile's validated IANA timezone; both midnight boundaries are calculated independently to account for daylight-saving transitions. Diary timestamps require an explicit offset and are stored in UTC.

`GET /api/v1/internal/users-contexts` requires `X-Internal-Token`. It returns only users who explicitly enabled proactive recommendations, with full allergy information and a five-minute delegated JWT. Secrets and password hashes are never returned.

Dependencies in addition to the common backend stack: `pwdlib[argon2]`, `email-validator`, `httpx`.
