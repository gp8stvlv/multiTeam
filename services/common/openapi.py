from copy import deepcopy
from threading import RLock

from fastapi import FastAPI

TEAM_SCHEME = "TeamAPIKey"
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
AUTH_PATHS = {"/api/v1/auth/register", "/api/v1/auth/login"}
GATEWAY_DESCRIPTION = (
    "Публичный HTTPS gateway требует общий X-API-Key. Для защищённых операций "
    "он передаётся вместе с пользовательским Bearer JWT; регистрация и вход "
    "требуют только X-API-Key. Локальный и внутренний доступ не требуют этого ключа."
)


def install_gateway_security_schema(app: FastAPI) -> None:
    """Document edge-only access without changing backend authentication."""
    if getattr(app.state, "gateway_security_schema_installed", False):
        return
    original_openapi = app.openapi
    cached_original = None
    cached_schema = None
    lock = RLock()

    def gateway_openapi():
        nonlocal cached_original, cached_schema
        with lock:
            original = original_openapi()
            if original is not cached_original:
                schema = deepcopy(original)
                schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
                schemes[TEAM_SCHEME] = {
                    "type": "apiKey", "in": "header", "name": "X-API-Key",
                    "description": GATEWAY_DESCRIPTION,
                }
                root_security = schema.get("security", [])
                for path, path_item in schema.get("paths", {}).items():
                    if not path.startswith("/api/v1/") or path.rstrip("/") == "/api/v1/internal" or path.startswith("/api/v1/internal/"):
                        continue
                    for method, operation in path_item.items():
                        if method not in HTTP_METHODS or operation.get("x-internal") or operation.get("x-private"):
                            continue
                        if path.rstrip("/") in AUTH_PATHS:
                            operation["security"] = [{TEAM_SCHEME: []}]
                            continue
                        required = {}
                        for alternative in operation.get("security", root_security):
                            for name, scopes in alternative.items():
                                required[name] = list(dict.fromkeys(required.get(name, []) + scopes))
                        if not required:
                            schemes.setdefault("HTTPBearer", {"type": "http", "scheme": "bearer"})
                            required["HTTPBearer"] = []
                        required[TEAM_SCHEME] = []
                        # One security object means AND; separate objects would permit a bypass.
                        operation["security"] = [required]
                info = schema.setdefault("info", {})
                description = info.get("description", "")
                info["description"] = (description + "\n\n" if description else "") + GATEWAY_DESCRIPTION
                cached_original, cached_schema = original, schema
            # Keep the original FastAPI schema and other callers' snapshots independent.
            return deepcopy(cached_schema)

    app.openapi = gateway_openapi
    app.state.gateway_security_schema_installed = True
