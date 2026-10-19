"""Import reviewed restaurant menus through the API; never print credentials.

The source files live outside Git in tmp/pdfs. See README for their official URLs.
Public writes require an explicit --base-url and an administrator session.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
import unicodedata
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts/menu-data/verified-restaurants.json"
ESTIMATES = ROOT / "scripts/menu-data/calorie-estimates.json"


def local_settings():
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


class Client:
    def __init__(self, base, api_key=""):
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.token = ""

    def call(self, method, path, data=None, *, content_type="application/json"):
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if data is not None:
            headers["Content-Type"] = content_type
            if content_type == "application/json":
                data = json.dumps(data, ensure_ascii=False).encode()
        request = Request(self.base + path, method=method, data=data, headers=headers)
        try:
            with urlopen(request, timeout=180) as response:
                return json.load(response)
        except HTTPError as exc:
            body = exc.read(1000).decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path}: HTTP {exc.code} {body}") from exc

    def login(self, email, password):
        result = self.call("POST", "/auth/login", {"email": email, "password": password})
        self.token = result["access_token"]
        if result.get("user", {}).get("role") != "admin":
            raise RuntimeError("Menu import requires an admin account")

    def restaurants(self):
        items = []
        while True:
            page = self.call("GET", f"/restaurants?limit=100&offset={len(items)}")
            items.extend(page["items"])
            if len(items) >= page["total"]:
                return items

    def upload(self, restaurant_id, source_kind, path):
        boundary = "gastrogid-" + uuid4().hex
        parts = []

        def field(name, content, filename=None, mime=None):
            header = f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\""
            if filename:
                header += f'; filename="{filename}"'
            header += "\r\n"
            if mime:
                header += f"Content-Type: {mime}\r\n"
            parts.append(header.encode() + b"\r\n" + content + b"\r\n")

        field("restaurant_id", restaurant_id.encode())
        field("source_kind", source_kind.encode())
        field("file", path.read_bytes(), path.name, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        return self.call("POST", "/menu-imports", body, content_type=f"multipart/form-data; boundary={boundary}")


def normalized(value):
    return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")


def find_evidence(source_kind, item, evidence):
    candidates = evidence
    if source_kind == "pdf":
        candidates = [part for part in evidence if part["page_number"] == item["source_page"]]
        candidates.sort(key=lambda part: len(part["fragment_text"]), reverse=True)
    elif source_kind == "html":
        name = normalized(item["name"])
        candidates = [part for part in evidence if name in normalized(part["fragment_text"])]
        candidates.sort(key=lambda part: len(part["fragment_text"]))
    if not candidates:
        raise RuntimeError(f"Source evidence missing for {item['name']}")
    return candidates[0]["id"]


def import_one(client, restaurant, existing, *, replace_menus=False, estimates=None):
    name = restaurant["name"]
    row = next((item for item in existing if item["name"] == name and item["address"] == restaurant["address"]), None)
    if row is None:
        fields = {key: restaurant[key] for key in ("name", "address", "location", "cuisines", "website")}
        row = client.call("POST", "/restaurants", fields)
        print(f"Created {name}", flush=True)
    if row["active_menu_version_id"] and not replace_menus:
        print(f"Skipped existing menu for {name}", flush=True)
        return

    previous_items = {}
    if row["active_menu_version_id"]:
        menu = client.call("GET", f"/restaurants/{row['id']}/menu")
        previous_items = {item["name"]: item for item in menu["items"]}
        if len(previous_items) != len(menu["items"]):
            raise RuntimeError(f"Duplicate published menu item names for {name}")
        if estimates is not None:
            expected = {item["name"]: item["price_minor"] for item in restaurant["items"]}
            actual = {item["name"]: item["price_minor"] for item in menu["items"]}
            if actual != expected:
                raise RuntimeError(f"Published menu has changed since source review for {name}; refusing to replace it")

    source = ROOT / restaurant["source_file"]
    if not source.is_file():
        raise RuntimeError(f"Source file missing: {source}; download it from {restaurant['source_url']}")
    job = client.upload(row["id"], restaurant["source_kind"], source)
    for _ in range(120):
        job = client.call("GET", f"/menu-imports/{job['id']}")
        if job["status"] in ("needs_review", "failed"):
            break
        time.sleep(2)
    if job["status"] != "needs_review":
        raise RuntimeError(f"Import of {name} failed: {job.get('error') or job['status']}")
    draft = client.call("GET", f"/menu-imports/{job['id']}/draft")
    evidence = client.call("GET", f"/menu-imports/{job['id']}/evidence")
    items = []
    for index, item in enumerate(restaurant["items"], 1):
        output = {key: item[key] for key in ("name", "description", "price_minor", "portion_weight_g") if key in item}
        if estimates is not None:
            output["nutrition"] = estimates[name][item["name"]]
        if item["name"] in previous_items:
            output["existing_dish_id"] = previous_items[item["name"]]["dish_id"]
        output["draft_key"] = f"verified-{index}"
        output["evidence_ids"] = [find_evidence(restaurant["source_kind"], item, evidence)]
        items.append(output)
    reviewed = client.call("PUT", f"/menu-imports/{job['id']}/draft", {
        "expected_revision": draft["revision"], "currency": "RUB", "items": items,
    })
    if len(reviewed["items"]) != len(items):
        raise RuntimeError(f"Draft item count changed for {name}")
    published = client.call("POST", f"/menu-imports/{job['id']}/publish", {"expected_revision": reviewed["revision"]})
    if len(published["items"]) != len(items):
        raise RuntimeError(f"Published item count changed for {name}")
    for item in published["items"]:
        if estimates is not None and any(
            item["nutrition"][field] != estimates[name][item["name"]][field]
            for field in ("calories_kcal", "protein_g", "fat_g", "carbs_g", "source")
        ):
            raise RuntimeError(f"Published nutrition estimate mismatch: {item['name']}")
        if item["name"] in previous_items and item["dish_id"] != previous_items[item["name"]]["dish_id"]:
            raise RuntimeError(f"Dish ID changed: {item['name']}")
    print(f"Published {name}: {len(items)} verified menu items", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/api/v1")
    parser.add_argument("--replace-menus", action="store_true")
    parser.add_argument("--deactivate-demos", action="store_true")
    parser.add_argument("--with-calorie-estimates", action="store_true",
                        help="Publish approximate calorie estimates from calorie-estimates.json")
    args = parser.parse_args()
    if args.with_calorie_estimates and not args.replace_menus:
        parser.error("--with-calorie-estimates requires --replace-menus")
    public = not args.base_url.startswith(("http://127.0.0.1:", "http://localhost:"))
    settings = local_settings() if not public else {}
    email = os.environ.get("GASTRO_ADMIN_EMAIL") or settings.get("DEMO_ADMIN_EMAIL")
    password = os.environ.get("GASTRO_ADMIN_PASSWORD") or settings.get("DEMO_ADMIN_PASSWORD")
    token = os.environ.get("GASTRO_JWT", "")
    api_key = os.environ.get("GASTRO_API_KEY", "")
    if (not token and (not email or not password)) or (public and not api_key):
        parser.error("Set GASTRO_JWT or administrator email/password and, for public API, GASTRO_API_KEY")
    client = Client(args.base_url, api_key)
    if token:
        client.token = token
    else:
        client.login(email, password)
    existing = client.restaurants()
    manifest = json.loads(MANIFEST.read_text())
    estimates = json.loads(ESTIMATES.read_text()) if args.with_calorie_estimates else None
    if estimates is not None:
        for restaurant in manifest:
            names = {item["name"] for item in restaurant["items"]}
            if set(estimates[restaurant["name"]]) != names:
                raise RuntimeError(f"Calorie estimate coverage mismatch for {restaurant['name']}")
            row = next((item for item in existing if item["name"] == restaurant["name"]
                        and item["address"] == restaurant["address"]), None)
            if row is None or not row["active_menu_version_id"]:
                raise RuntimeError(f"Current published menu missing for {restaurant['name']}")
            menu = client.call("GET", f"/restaurants/{row['id']}/menu")
            expected = {item["name"]: item["price_minor"] for item in restaurant["items"]}
            actual = {item["name"]: item["price_minor"] for item in menu["items"]}
            if actual != expected or len(menu["items"]) != len(expected):
                raise RuntimeError(f"Published menu has changed since source review for {restaurant['name']}; refusing to replace it")
    for restaurant in manifest:
        import_one(client, restaurant, existing, replace_menus=args.replace_menus, estimates=estimates)
    if args.deactivate_demos:
        for restaurant in client.restaurants():
            if restaurant["is_demo"]:
                client.call("PATCH", f"/restaurants/{restaurant['id']}", {"active": False})
                print(f"Deactivated sample restaurant: {restaurant['name']}", flush=True)
    after = client.restaurants()
    published_items = 0
    for restaurant in manifest:
        row = next((item for item in after if item["name"] == restaurant["name"] and
                    item["address"] == restaurant["address"]), None)
        if row is None or not row["active_menu_version_id"]:
            raise RuntimeError(f"Published restaurant missing from catalog: {restaurant['name']}")
        menu = client.call("GET", f"/restaurants/{row['id']}/menu")
        if len(menu["items"]) != len(restaurant["items"]):
            raise RuntimeError(f"Published menu item count mismatch for {restaurant['name']}")
        if estimates is not None and any(
            any(item["nutrition"][field] != estimates[restaurant["name"]][item["name"]][field]
                for field in ("calories_kcal", "protein_g", "fat_g", "carbs_g", "source"))
            for item in menu["items"]
        ):
            raise RuntimeError(f"Published calorie estimates mismatch for {restaurant['name']}")
        published_items += len(menu["items"])
    print(json.dumps({"active_total": len(after), "real": sum(not item["is_demo"] for item in after),
                      "demo": sum(item["is_demo"] for item in after), "published_items": published_items}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
