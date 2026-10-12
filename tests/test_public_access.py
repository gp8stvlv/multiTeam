"""Shared-key creation and real Nginx access-phase/proxy boundary checks."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIGURE = ROOT / "scripts/configure-public-access.py"


def configure(directory, *arguments):
    return subprocess.run(
        [sys.executable, str(CONFIGURE), "--directory", str(directory), *arguments],
        capture_output=True, text=True,
    )


def test_credential_generation_preservation_rotation_and_permissions(tmp_path):
    directory = tmp_path / "access"
    initial = configure(directory)
    assert initial.returncode == 0
    first = (directory / "team-key").read_text().strip()
    assert re.fullmatch(r"gg_team_[A-Za-z0-9_-]{43}", first)
    if first in initial.stdout or first in initial.stderr:
        pytest.fail("Default configuration disclosed the credential")
    for path, expected in [
        (directory, 0o700), (directory / "nginx", 0o755),
        (directory / "team-key", 0o600),
        (directory / "nginx/team-api-key.map", 0o600),
        (directory / "nginx/docs.htpasswd", 0o644),
    ]:
        assert path.stat().st_mode & 0o777 == expected
    assert configure(directory).returncode == 0
    if first != (directory / "team-key").read_text().strip():
        pytest.fail("A normal reconfiguration rotated the credential")
    assert configure(directory, "--rotate").returncode == 0
    second = (directory / "team-key").read_text().strip()
    if first == second:
        pytest.fail("Explicit rotation kept the old credential")
    shown = configure(directory, "--show")
    assert shown.returncode == 0
    if shown.stdout.strip() != second:
        pytest.fail("Explicit display did not return the stored credential")


def test_corrupt_existing_credential_is_not_silently_replaced(tmp_path):
    directory = tmp_path / "access"
    directory.mkdir()
    (directory / "team-key").write_text("invalid-existing-value\n")
    result = configure(directory)
    assert result.returncode == 1
    assert (directory / "team-key").read_text() == "invalid-existing-value\n"
    assert not (directory / "nginx/team-api-key.map").exists()


def test_credentials_cannot_be_written_to_the_checkout():
    result = configure(ROOT / "generated-access")
    assert result.returncode == 1
    assert not (ROOT / "generated-access").exists()


@pytest.mark.skipif(os.getenv("RUN_PUBLIC_NGINX_TESTS") != "1", reason="Set RUN_PUBLIC_NGINX_TESTS=1 to run real Docker Nginx")
def test_real_nginx_gates_api_docs_and_never_proxies_denied_requests(tmp_path):
    access = tmp_path / "access"
    assert configure(access).returncode == 0
    key = (access / "team-key").read_text().strip()
    basic = "Basic " + base64.b64encode(("team:" + key).encode()).decode()
    webroot = tmp_path / "webroot"
    challenge = webroot / ".well-known/acme-challenge/test-challenge"
    challenge.parent.mkdir(parents=True)
    challenge.write_text("public-acme-test")

    # Only transport/certificate directives change for this local HTTP test.
    # The production access and proxy locations are used verbatim by real Nginx.
    production = (ROOT / "infra/public/nginx-https.conf").read_text()
    transformed = "\n".join(
        line for line in production.splitlines()
        if not line.strip().startswith(("ssl_", "http2 on;"))
    ).replace("listen 443 ssl default_server;", "listen 8080 default_server;")
    transformed = transformed.replace("set $gateway gateway:80;", "set $gateway 127.0.0.1:8000;")
    transformed += """
map $http_authorization $test_jwt_seen {
    default false;
    "Bearer test-user-token" true;
}
map $http_authorization $test_basic_seen {
    default false;
    ~*^Basic true;
}
server {
    listen 8000;
    access_log /tmp/upstream-access.log combined;
    default_type application/json;
    add_header X-Test-JWT $test_jwt_seen always;
    add_header X-Test-Basic $test_basic_seen always;
    add_header X-Test-Team-Key $http_x_api_key always;
    location = / { return 302 /docs/; }
    location / { return 200 '{"ok":true}'; }
}
"""
    configuration = tmp_path / "nginx.conf"
    configuration.write_text(transformed)
    empty_access = tmp_path / "empty-access"
    empty_access.mkdir()
    missing = subprocess.run(
        ["docker", "run", "--rm",
         "--mount", f"type=bind,src={configuration},dst=/etc/nginx/conf.d/default.conf,readonly",
         "--mount", f"type=bind,src={empty_access},dst=/etc/gastrogid,readonly",
         "nginx:1.28-alpine", "nginx", "-t"],
        capture_output=True, text=True,
    )
    assert missing.returncode != 0 and "team-api-key.map" in missing.stderr
    container = "gastrogid-access-test-" + uuid4().hex[:12]

    def docker(*arguments):
        result = subprocess.run(["docker", *arguments], capture_output=True, text=True)
        if result.returncode:
            pytest.fail("Docker/Nginx test command failed: " + result.stderr)
        return result.stdout

    try:
        docker("run", "-d", "--name", container,
               "-p", "127.0.0.1::8080", "-p", "127.0.0.1::80",
               "--mount", f"type=bind,src={configuration},dst=/etc/nginx/conf.d/default.conf,readonly",
               "--mount", f"type=bind,src={access / 'nginx'},dst=/etc/gastrogid,readonly",
               "--mount", f"type=bind,src={webroot},dst=/var/www/certbot,readonly",
               "nginx:1.28-alpine")
        bindings = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", container))
        address = "http://127.0.0.1:" + bindings["8080/tcp"][0]["HostPort"]
        clear_address = "http://127.0.0.1:" + bindings["80/tcp"][0]["HostPort"]
        deadline = time.monotonic() + 15
        with httpx.Client(base_url=address, timeout=5, follow_redirects=False) as client:
            while True:
                try:
                    if client.get("/health").status_code == 403:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() >= deadline:
                    pytest.fail("Nginx did not become ready")
                time.sleep(0.1)

            def upstream_count():
                return len(docker("exec", container, "cat", "/tmp/upstream-access.log").splitlines())

            assert upstream_count() == 0
            for path, method in [("/health", "GET"), ("/api/v1/restaurants", "GET"),
                                 ("/api/v1/auth/login", "POST"), ("/api/v1/auth/register", "POST")]:
                for headers in ({}, {"X-API-Key": "wrong"}, {"X-API-Key": key.swapcase()},
                                {"Authorization": basic}):
                    response = client.request(method, path, headers=headers)
                    assert response.status_code == 403
                    error = response.json()
                    assert error["code"] == "invalid_api_key" and error["message"]
                    assert error["request_id"] and "details" in error
                    assert "www-authenticate" not in response.headers
            assert upstream_count() == 0
            for path in ("/", "/docs/", "/docs/swagger-ui-bundle.js", "/openapi/catalog.json",
                         "/openapi/users.json", "/openapi/recommendations.json"):
                response = client.get(path)
                assert response.status_code == 401
                assert response.headers["www-authenticate"].startswith("Basic ")
            assert upstream_count() == 0
            assert client.get("/api/v1/internal/users-contexts").status_code == 404
            assert client.get("/health/catalog").status_code == 404
            assert upstream_count() == 0

            allowed = {"X-API-Key": key}
            assert client.get("/health", headers=allowed).status_code == 200
            assert upstream_count() == 0
            response = client.get("/api/v1/me/profile", headers={**allowed, "Authorization": "Bearer test-user-token"})
            assert response.status_code == 200 and response.headers["x-test-jwt"] == "true"
            assert response.headers["x-test-basic"] == "false"
            assert "x-test-team-key" not in response.headers
            expected = 1
            for path in ("/docs/", "/openapi/catalog.json", "/openapi/users.json", "/openapi/recommendations.json"):
                for headers in (allowed, {"Authorization": basic}):
                    response = client.get(path, headers=headers)
                    assert response.status_code == 200
                    assert response.headers["x-test-basic"] == "false"
                    assert "x-test-team-key" not in response.headers
                    expected += 1
            response = client.get("/", headers={"Authorization": basic})
            assert response.status_code == 302 and response.headers["x-test-basic"] == "false"
            expected += 1
            assert upstream_count() == expected
        with httpx.Client(base_url=clear_address, timeout=5, follow_redirects=False) as clear:
            assert clear.get("/.well-known/acme-challenge/test-challenge").text == "public-acme-test"
            assert clear.get("/api/v1/restaurants").status_code == 308
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
