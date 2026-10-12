import ast
from copy import deepcopy
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Security
from fastapi.security import APIKeyHeader, HTTPBearer
from fastapi.testclient import TestClient
from pydantic import BaseModel

from services.common.openapi import GATEWAY_DESCRIPTION, install_gateway_security_schema


class PublicResponse(BaseModel):
    name: str


def sample_app():
    app = FastAPI(title="Schema test", description="Existing description")
    bearer = HTTPBearer()
    internal = APIKeyHeader(name="X-Internal-Token", scheme_name="InternalToken")

    @app.get("/api/v1/items", response_model=PublicResponse, tags=["Items"], operation_id="listItems",
             openapi_extra={"x-owner": "catalog"})
    def items(token=Security(bearer)):
        return {"name": "fixture"}

    @app.post("/api/v1/auth/register", status_code=201, response_model=PublicResponse)
    def register():
        return {"name": "fixture"}

    @app.post("/api/v1/auth/login", response_model=PublicResponse)
    def login():
        return {"name": "fixture"}

    @app.get("/api/v1/internal/context")
    def context(key=Security(internal)):
        return {"status": "ok"}

    @app.get("/api/v1/private-report", openapi_extra={"x-private": True})
    def private_report(key=Security(internal)):
        return {"status": "ok"}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/private/status")
    def private_status(key=Security(internal)):
        return {"status": "ok"}

    return app


def test_gateway_and_bearer_are_required_in_one_security_object():
    app = sample_app()
    original = deepcopy(app.openapi())
    install_gateway_security_schema(app)
    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["TeamAPIKey"]["type"] == "apiKey"
    assert schema["components"]["securitySchemes"]["TeamAPIKey"]["in"] == "header"
    assert schema["components"]["securitySchemes"]["TeamAPIKey"]["name"] == "X-API-Key"
    assert schema["paths"]["/api/v1/items"]["get"]["security"] == [{"HTTPBearer": [], "TeamAPIKey": []}]
    for path in ["/api/v1/auth/register", "/api/v1/auth/login"]:
        assert schema["paths"][path]["post"]["security"] == [{"TeamAPIKey": []}]
    for path in ["/api/v1/internal/context", "/api/v1/private-report", "/health", "/private/status"]:
        assert schema["paths"][path] == original["paths"][path]
    expected_operation = deepcopy(original["paths"]["/api/v1/items"]["get"])
    expected_operation["security"] = [{"HTTPBearer": [], "TeamAPIKey": []}]
    assert schema["paths"]["/api/v1/items"]["get"] == expected_operation
    assert schema["components"]["schemas"] == original["components"]["schemas"]
    assert schema["info"]["description"] == "Existing description\n\n" + GATEWAY_DESCRIPTION
    assert app.openapi_schema == original


def test_cached_schema_and_other_apps_do_not_share_mutable_state():
    app = sample_app()
    install_gateway_security_schema(app)
    install_gateway_security_schema(app)
    first = app.openapi()
    first["paths"]["/api/v1/items"]["get"]["security"][0].clear()
    first["components"]["schemas"]["PublicResponse"]["properties"]["name"]["type"] = "integer"
    first["components"]["securitySchemes"]["TeamAPIKey"]["name"] = "WrongHeader"
    second = app.openapi()
    assert second["paths"]["/api/v1/items"]["get"]["security"] == [{"HTTPBearer": [], "TeamAPIKey": []}]
    assert second["components"]["schemas"]["PublicResponse"]["properties"]["name"]["type"] == "string"
    assert second["components"]["securitySchemes"]["TeamAPIKey"]["name"] == "X-API-Key"
    other = sample_app()
    install_gateway_security_schema(other)
    assert other.openapi() == second
    assert second["info"]["description"].count(GATEWAY_DESCRIPTION) == 1


def test_cache_reset_uses_original_fastapi_generation_and_preserves_global_security():
    app = sample_app()
    base = app.openapi()
    base["security"] = [{"HTTPBearer": []}]
    install_gateway_security_schema(app)
    assert app.openapi()["security"] == [{"HTTPBearer": []}]

    @app.get("/api/v1/extra", response_model=PublicResponse)
    def extra():
        return {"name": "fixture"}

    app.openapi_schema = None
    refreshed = app.openapi()
    assert "/api/v1/extra" in refreshed["paths"]
    assert refreshed["paths"]["/api/v1/extra"]["get"]["security"] == [{"HTTPBearer": [], "TeamAPIKey": []}]


def test_schema_documentation_does_not_enforce_team_key_on_private_backend():
    app = sample_app()
    install_gateway_security_schema(app)
    with TestClient(app) as client:
        assert client.get("/api/v1/items", headers={"Authorization": "Bearer explicit-test-jwt"}).status_code == 200
        assert client.post("/api/v1/auth/login").status_code == 200


@pytest.fixture
def smoke_gateway_helpers():
    # Compile the actual pure functions without executing the CLI or reading .env.
    path = Path(__file__).resolve().parents[1] / "scripts" / "smoke.py"
    source = ast.parse(path.read_text())
    functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in {"gateway_headers", "require_gateway_access"}]
    assert len(functions) == 2
    namespace = {"os": os}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["gateway_headers"], namespace["require_gateway_access"]


def test_smoke_environment_key_has_priority_and_keeps_jwt_separate(smoke_gateway_helpers):
    headers, _ = smoke_gateway_helpers
    key = "explicit-test-gateway-key"
    assert headers({"GASTRO_API_KEY": "file-fixture"}, {"GASTRO_API_KEY": key}) == {"X-API-Key": key}
    assert headers({"GASTRO_API_KEY": "file-fixture"}, {}) == {"X-API-Key": "file-fixture"}
    assert headers({"GASTRO_API_KEY": "file-fixture"}, {"GASTRO_API_KEY": ""}) == {}
    assert headers({}, {}) == {}
    received = []

    def transport(request):
        received.append(request)
        return httpx.Response(200, json={"status": "ok"})

    with httpx.Client(base_url="https://gateway.test/api/v1", headers=headers({}, {"GASTRO_API_KEY": key}),
                      transport=httpx.MockTransport(transport)) as client:
        client.get("https://gateway.test/health")
        client.get("https://gateway.test/openapi/catalog.json")
        client.post("/auth/login")
        client.headers["Authorization"] = "Bearer explicit-test-jwt"
        client.get("/me/profile")
        client.get("/me/feed", headers={"Authorization": "Bearer explicit-admin-test-jwt"})
    assert len(received) == 5 and all(request.headers["X-API-Key"] == key for request in received)
    assert "Authorization" not in received[2].headers
    assert received[3].headers["Authorization"] == "Bearer explicit-test-jwt"
    assert received[4].headers["Authorization"] == "Bearer explicit-admin-test-jwt"


def test_smoke_public_forbidden_is_immediate_and_never_echoes_token(smoke_gateway_helpers):
    headers, require_access = smoke_gateway_helpers
    secret_fixture = "explicit-test-value-that-must-not-appear-in-errors"
    response = httpx.Response(403, json={"code": "api_key_invalid", "debug": secret_fixture})
    with pytest.raises(AssertionError) as failure:
        require_access(response)
    assert "GASTRO_API_KEY" in str(failure.value)
    assert secret_fixture not in str(failure.value)
    assert response.status_code == 403
    require_access(httpx.Response(200, json={"status": "ok"}))
    with pytest.raises(ValueError) as bad_header:
        headers({}, {"GASTRO_API_KEY": secret_fixture + "\n"})
    assert secret_fixture not in str(bad_header.value)
