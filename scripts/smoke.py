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
parser.add_argument("--agents", action="store_true", help="Require and exercise the real configured LLM")
parser.add_argument("--imports", action="store_true", help="Create an inactive integration restaurant and publish an HTML menu")
args = parser.parse_args()
settings = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
client = httpx.Client(base_url=args.url.rstrip("/") + "/api/v1", timeout=60)


def call(method, path, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != expected:
        try:
            code = response.json().get("code", "unexpected_response")
        except ValueError:
            code = "unexpected_response"
        raise AssertionError(f"{method} {path}: HTTP {response.status_code}, {code}")
    return response.json() if response.content else None


def wait(path, done, limit=90):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        data = call("GET", path)
        if data["status"] in done:
            return data
        if data["status"] == "failed":
            raise AssertionError("Background job failed: " + data["error"]["code"])
        time.sleep(1)
    raise AssertionError("Background job timeout")


try:
    deadline = time.monotonic() + 60
    while True:
        try:
            live = all(client.get(args.url.rstrip("/") + "/health/" + service).status_code == 200
                       for service in ("catalog", "users", "recommendations"))
        except httpx.HTTPError:
            live = False
        if live:
            break
        if time.monotonic() >= deadline:
            raise AssertionError("Gateway or backend did not become live")
        time.sleep(1)
    nonce = uuid4().hex[:12]
    auth = call("POST", "/auth/register", expected=201,
                json={"email": f"smoke-{nonce}@example.test", "password": secrets.token_urlsafe(24)})
    client.headers["Authorization"] = "Bearer " + auth["access_token"]
    profile = call("GET", "/me/profile")
    assert profile["user_id"] == auth["user"]["id"]
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
        result = wait("/recommendations/" + run["id"], {"completed"}, limit=600)
        assert result["result"]["outcome"] == "ready" and result["result"]["options"]
        option = result["result"]["options"][0]
        assert option["total_price_minor"] <= 100000 and option["evidence_ids"]
        assert any("хинкали" in i["name"].casefold() for i in option["items"])
        print("PASS live GLM/LangGraph tool-using recommendation with validated evidence")
    else:
        response = client.post("/recommendations", json={"mode": "dish_search", "query": "хинкали"}, headers={"Idempotency-Key": str(uuid4())})
        if not settings.get("LLM_API_KEY"):
            assert response.status_code == 503 and response.json()["code"] == "llm_not_configured"
        print("SKIP live LLM inference (--agents requires a configured API key)")

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
    print("LIVE API SMOKE PASSED:", args.url)
except Exception as exc:
    # Assertion messages are carefully constructed to avoid echoing authenticated responses.
    print("SMOKE FAILED:", str(exc) if isinstance(exc, AssertionError) else type(exc).__name__, file=sys.stderr)
    raise SystemExit(1)
finally:
    client.close()
