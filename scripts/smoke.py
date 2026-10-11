"""Live stack verification. Never prints passwords, JWTs or API keys."""
from datetime import datetime, timezone
from decimal import Decimal
import argparse
from pathlib import Path
import secrets
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo
import httpx
from dotenv import dotenv_values

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8080")
parser.add_argument("--public", action="store_true", help="Verify the public HTTPS gateway without private service health endpoints")
parser.add_argument("--agents", action="store_true", help="Require and exercise the real configured LLM")
parser.add_argument("--imports", action="store_true", help="Create an inactive integration restaurant and publish an HTML menu")
parser.add_argument("--proactive", action="store_true", help="With --agents, verify the real scheduler-generated feed and disable opt-in afterward")
parser.add_argument("--proactive-timeout", type=int, default=1800, help="Maximum seconds to wait for the real proactive scheduler (default: 1800)")
args = parser.parse_args()
if args.proactive and not args.agents:
    parser.error("--proactive requires --agents")
if args.proactive_timeout < 1:
    parser.error("--proactive-timeout must be positive")
settings = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
client = httpx.Client(base_url=args.url.rstrip("/") + "/api/v1", timeout=60)
smoke_token = None
smoke_email = None
smoke_password = None
proactive_cleanup_required = False


def call(method, path, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != expected:
        try:
            code = response.json().get("code", "unexpected_response")
        except ValueError:
            code = "unexpected_response"
        raise AssertionError(f"{method} {path}: HTTP {response.status_code}, {code}")
    return response.json() if response.content else None


def wait(path, done, limit=90, progress_label=None):
    started = time.monotonic()
    deadline = started + limit
    next_progress = started + 30
    while time.monotonic() < deadline:
        data = call("GET", path)
        if data["status"] in done:
            return data
        if data["status"] == "failed":
            raise AssertionError("Background job failed: " + data["error"]["code"])
        if progress_label and time.monotonic() >= next_progress:
            print(f"WAIT {progress_label}: {int(time.monotonic() - started)} seconds", flush=True)
            next_progress = time.monotonic() + 30
        time.sleep(1)
    raise AssertionError("Background job timeout")


def verify_ready_recommendation(run, budget_minor, calorie_limit=None):
    """Verify each alternative against owning APIs, not model-written numbers."""
    assert run["status"] == "completed", "Recommendation is not completed"
    result = run["result"]
    assert result["outcome"] == "ready" and result["options"], "No ready recommendation alternatives"
    nutrients = ("calories_kcal", "protein_g", "fat_g", "carbs_g")
    for option in result["options"]:
        assert option["items"], "Recommendation has no menu items"
        restaurant = call("GET", "/restaurants/" + option["restaurant"]["id"])
        assert restaurant["active"], "Recommendation uses an inactive restaurant"
        selected_ids, versions, currencies, expected_evidence = set(), set(), set(), set()
        price = 0
        sums = {field: Decimal(0) for field in nutrients}
        complete = dict.fromkeys(nutrients, True)
        for selected in option["items"]:
            item_id, portions = selected["menu_item_id"], selected["portions"]
            assert item_id not in selected_ids, "A menu item appears twice in an alternative"
            assert isinstance(portions, int) and not isinstance(portions, bool) and 1 <= portions <= 20, "Invalid ordered portion count"
            selected_ids.add(item_id)
            item = call("GET", "/menu-items/" + item_id)
            assert item["id"] == item_id and item["dish_id"] == selected["dish_id"], "Menu item identity mismatch"
            assert item["restaurant_id"] == restaurant["id"], "Alternative combines multiple restaurants"
            assert item["menu_version_id"] == restaurant["active_menu_version_id"], "Alternative uses an archived menu"
            assert selected["unit_price_minor"] == item["price_minor"], "Unit price does not match the menu"
            assert selected["nutrition_per_portion"] == item["nutrition"], "Nutrition facts do not match source menu data"
            versions.add(item["menu_version_id"])
            currencies.add(item["currency"])
            price += item["price_minor"] * portions
            expected_evidence.update(item["evidence_ids"])
            expected_evidence.update(item["nutrition"].get("evidence_ids", []))
            expected_evidence.update(a["evidence_id"] for a in item["allergens"] if a.get("evidence_id"))
            for field in nutrients:
                value = item["nutrition"][field]
                if value is None:
                    complete[field] = False
                else:
                    sums[field] += Decimal(str(value)) * portions
        assert len(versions) == 1 and currencies == {option["currency"]}, "Incoherent menu version or currency"
        assert option["total_price_minor"] == price and price <= budget_minor, "Total price is incorrect or exceeds budget"
        assert option["evidence_ids"] and expected_evidence.issubset(option["evidence_ids"]), "Source evidence is incomplete"
        for evidence_id in option["evidence_ids"]:
            proof = call("GET", "/evidence/" + evidence_id)
            assert proof["id"] == evidence_id and proof["restaurant_id"] == restaurant["id"], "Evidence belongs to a different source"
            assert proof.get("menu_item_id") is None or proof["menu_item_id"] in selected_ids, "Evidence belongs to an unrelated menu item"
        for field in nutrients:
            actual = option["nutrition"][field]
            assert Decimal(str(option["nutrition"]["known_subtotals"][field])) == sums[field], "Known nutrition subtotal is incorrect"
            if complete[field]:
                assert actual is not None and Decimal(str(actual)) == sums[field], "Aggregate nutrition is incorrect"
            else:
                assert actual is None, "Unknown nutrition was treated as a known total"
        if calorie_limit is not None:
            assert complete["calories_kcal"] and sums["calories_kcal"] <= Decimal(str(calorie_limit)), "Aggregate calories are unknown or exceed the limit"


def daytime_timezone():
    """Etc/GMT names invert signs; choose a real IANA zone whose current hour is 14."""
    now = datetime.now(timezone.utc)
    offset = (14 - now.hour + 12) % 24 - 12
    name = "Etc/GMT" if offset == 0 else f"Etc/GMT{-offset:+d}"
    assert now.astimezone(ZoneInfo(name)).hour == 14, "Could not choose a daytime timezone"
    return name


def wait_proactive_feed(limit):
    started = time.monotonic()
    deadline = started + limit
    next_progress = started + 30
    print("WAIT real scheduler-generated proactive recommendation", flush=True)
    while time.monotonic() < deadline:
        feed = call("GET", "/me/feed")
        for entry in feed["items"]:
            run = call("GET", "/recommendations/" + entry["recommendation_id"])
            assert run["id"] == entry["recommendation_id"], "Feed recommendation association is incorrect"
            assert run["trigger"] == "proactive", "Feed item was not created by the proactive scheduler"
            assert run["status"] == "completed", "Feed exposed an unfinished recommendation"
            return run
        if time.monotonic() >= next_progress:
            print(f"WAIT real proactive scheduler: {int(time.monotonic() - started)} seconds", flush=True)
            next_progress = time.monotonic() + 30
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise AssertionError("No scheduler-generated ready recommendation before proactive timeout")


try:
    deadline = time.monotonic() + 60
    while True:
        try:
            if args.public:
                gateway = client.get(args.url.rstrip("/") + "/health")
                schemas = [client.get(args.url.rstrip("/") + "/openapi/" + service + ".json")
                           for service in ("catalog", "users", "recommendations")]
                live = gateway.status_code == 200 and gateway.json().get("status") == "ok" and all(
                    response.status_code == 200 and response.json().get("paths")
                    for response in schemas
                )
            else:
                live = all(client.get(args.url.rstrip("/") + "/health/" + service).status_code == 200
                           for service in ("catalog", "users", "recommendations"))
        except (httpx.HTTPError, ValueError):
            live = False
        if live:
            break
        if time.monotonic() >= deadline:
            raise AssertionError("Gateway or backend did not become live")
        time.sleep(1)
    nonce = uuid4().hex[:12]
    smoke_email, smoke_password = f"smoke-{nonce}@example.test", secrets.token_urlsafe(24)
    auth = call("POST", "/auth/register", expected=201,
                json={"email": smoke_email, "password": smoke_password})
    smoke_token = auth["access_token"]
    client.headers["Authorization"] = "Bearer " + smoke_token
    profile = call("GET", "/me/profile")
    assert profile["user_id"] == auth["user"]["id"]
    if args.public:
        # Exercise the recommendations service through its real owner-scoped
        # public route without creating a model request or enabling the feed.
        assert isinstance(call("GET", "/me/feed")["items"], list)
    restaurants = call("GET", "/restaurants")["items"]
    assert len(restaurants) >= 5
    menu = call("GET", f"/restaurants/{restaurants[0]['id']}/menu")
    item = menu["items"][0]
    evidence = call("GET", f"/evidence/{item['evidence_ids'][0]}")
    assert evidence["restaurant_id"] == item["restaurant_id"]
    print("PASS auth, catalog, versioned menu and source evidence")

    deadline = time.monotonic() + 180
    while True:
        response = client.post("/search", json={"query": "хочу хинкали", "constraints": {}, "limit": 5})
        if response.status_code == 200:
            break
        if response.status_code != 503 or time.monotonic() > deadline:
            raise AssertionError(f"Real semantic search not ready: HTTP {response.status_code}")
        time.sleep(2)
    search = response.json()
    assert "TEST" not in search["embedding_model"] and search["items"]
    assert any("хинкали" in hit["item"]["name"].casefold() for hit in search["items"])
    print("PASS real semantic retrieval:", search["embedding_model"], "top dishes:", ", ".join(h["item"]["name"] for h in search["items"][:3]))

    call("PATCH", "/me/profile", json={"allergies": [{"code": "milk"}], "daily_calories_target": 2200})
    filtered = call("POST", "/search", json={"query": "что-нибудь вкусное", "constraints": {}, "limit": 20})
    for hit in filtered["items"]:
        assert any(a["code"] == "milk" and a["status"] == "declared_absent" and a.get("evidence_id") for a in hit["item"]["allergens"])
    call("PATCH", "/me/profile", json={"allergies": []})
    print("PASS mandatory profile allergy merge")

    payload = {"kind": "menu_item", "menu_item_id": item["id"], "portions": 0.5,
               "consumed_at": datetime.now(timezone.utc).isoformat(), "note": "Integration smoke"}
    headers = {"Idempotency-Key": str(uuid4())}
    entry = call("POST", "/me/diary", expected=201, json=payload, headers=headers)
    retry = call("POST", "/me/diary", expected=201, json=payload, headers=headers)
    assert retry["id"] == entry["id"]
    value = item["nutrition"]["calories_kcal"]
    if value is not None:
        assert Decimal(str(entry["consumed_nutrition"]["calories_kcal"])) == Decimal(str(value)) / 2
    day = datetime.now(ZoneInfo(profile["timezone"])).date().isoformat()
    summary = call("GET", "/me/daily-summary", params={"date": day})
    assert summary["entry_count"] == 1
    call("DELETE", f"/me/diary/{entry['id']}", expected=204)
    assert call("GET", "/me/daily-summary", params={"date": day})["entry_count"] == 0
    print("PASS fractional diary, exact calories, idempotency and daily summary")

    if args.agents:
        request = {"mode": "dish_search", "query": "Хочу хинкали, предложи один вариант", "constraints": {"budget_minor": 100000}, "max_options": 1}
        run = call("POST", "/recommendations", expected=202, json=request, headers={"Idempotency-Key": str(uuid4())})
        result = wait("/recommendations/" + run["id"], {"completed"}, limit=600, progress_label="live dish_search")
        verify_ready_recommendation(result, budget_minor=100000)
        option = result["result"]["options"][0]
        assert any("хинкали" in i["name"].casefold() for i in option["items"])
        print("PASS live GLM/LangGraph dish_search with validated menu, budget and evidence")

        meal = {"mode": "meal_plan", "query": "Подбери обед из меню одного ресторана до 1000 рублей и 900 ккал; можно сочетать несколько блюд.",
                "constraints": {"budget_minor": 100000, "max_calories_kcal": 900}, "max_options": 1}
        run = call("POST", "/recommendations", expected=202, json=meal, headers={"Idempotency-Key": str(uuid4())})
        result = wait("/recommendations/" + run["id"], {"completed"}, limit=600, progress_label="live meal_plan")
        verify_ready_recommendation(result, budget_minor=100000, calorie_limit=900)
        print("PASS live GLM/LangGraph meal_plan with coherent restaurant, exact totals and evidence")

        if args.proactive:
            proactive_cleanup_required = True
            call("PATCH", "/me/profile", json={"proactive_enabled": True, "timezone": daytime_timezone(),
                "daily_calories_target": 2200, "default_budget_minor": 100000, "currency": "RUB"})
            result = wait_proactive_feed(args.proactive_timeout)
            verify_ready_recommendation(result, budget_minor=100000, calorie_limit=2200)
            print("PASS real scheduler-generated live GLM proactive recommendation with confirmed origin and evidence")
    else:
        if not args.public:
            health_response = client.get(args.url.rstrip("/") + "/health/recommendations")
            assert health_response.status_code == 200, "Recommendations health check failed"
            if not health_response.json()["llm_configured"]:
                response = client.post("/recommendations", json={"mode": "dish_search", "query": "хинкали"}, headers={"Idempotency-Key": str(uuid4())})
                assert response.status_code == 503 and response.json()["code"] == "llm_not_configured"
        print("SKIP live LLM inference (use --agents to exercise the configured provider)")

    if args.imports:
        old_token = client.headers["Authorization"]
        admin = call("POST", "/auth/login", json={"email": settings["DEMO_ADMIN_EMAIL"], "password": settings["DEMO_ADMIN_PASSWORD"]})
        client.headers["Authorization"] = "Bearer " + admin["access_token"]
        restaurant = call("POST", "/restaurants", expected=201, json={"name": "Integration fixture " + nonce,
                          "address": "Тестовый адрес", "location": {"latitude": 55.75, "longitude": 37.62}, "cuisines": ["test"], "active": False})
        html = '<html><div class="menu-item" data-name="Chicken rice" data-price="500" data-ingredients="chicken,rice" data-ingredients-complete="true" data-nutrition-basis="per_portion" data-kcal="450" data-protein-g="30">Chicken rice | 500 RUB</div></html>'
        job = call("POST", "/menu-imports", expected=202, data={"restaurant_id": restaurant["id"], "source_kind": "html"},
                   files={"file": ("smoke.html", html.encode(), "text/html")})
        wait("/menu-imports/" + job["id"], {"needs_review"}, limit=90)
        draft = call("GET", f"/menu-imports/{job['id']}/draft")
        assert draft["items"][0]["price_minor"] == 50000 and draft["items"][0]["nutrition"]["calories_kcal"] == 450
        published = call("POST", f"/menu-imports/{job['id']}/publish", expected=201, json={"expected_revision": draft["revision"]})
        proof = call("GET", f"/evidence/{published['items'][0]['evidence_ids'][0]}")
        assert "Chicken rice" in proof["fragment_text"]
        client.headers["Authorization"] = old_token
        print("PASS persistent HTML upload, background extraction, review, publish and evidence")
except Exception as exc:
    # Assertion messages are carefully constructed to avoid echoing authenticated responses.
    print("SMOKE FAILED:", str(exc) if isinstance(exc, AssertionError) else type(exc).__name__, file=sys.stderr)
    raise SystemExit(1)
finally:
    cleanup_failed = False
    if proactive_cleanup_required and smoke_token:
        try:
            # Imports may have switched the default Authorization to admin; use
            # only the smoke user's token when disabling their explicit opt-in.
            response = client.patch("/me/profile", json={"proactive_enabled": False},
                                    headers={"Authorization": "Bearer " + smoke_token})
            if response.status_code == 401:
                # A user-selected long scheduler timeout can outlive the JWT.
                # Credentials remain only in memory and are never logged.
                refreshed = client.post("/auth/login", json={"email": smoke_email, "password": smoke_password})
                if refreshed.status_code == 200:
                    response = client.patch("/me/profile", json={"proactive_enabled": False},
                        headers={"Authorization": "Bearer " + refreshed.json()["access_token"]})
            assert response.status_code == 200 and response.json()["proactive_enabled"] is False, "Proactive opt-in cleanup failed"
        except Exception:
            cleanup_failed = True
            print("SMOKE CLEANUP FAILED: could not disable proactive recommendations for the isolated smoke account", file=sys.stderr)
    client.close()
    if cleanup_failed:
        raise SystemExit(1)

print("LIVE API SMOKE PASSED:", args.url)
