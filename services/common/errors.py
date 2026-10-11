import logging
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def install_error_handlers(app: FastAPI):
    original_openapi = app.openapi
    def documented_openapi():
        schema = original_openapi()
        schema.setdefault("components", {}).setdefault("schemas", {})["ApiError"] = {
            "type": "object", "required": ["code", "message", "request_id", "details"],
            "properties": {"code": {"type": "string"}, "message": {"type": "string"},
                           "request_id": {"type": "string"}, "details": {}}}
        for path, operations in schema["paths"].items():
            for operation in operations.values():
                if not isinstance(operation, dict) or "responses" not in operation:
                    continue
                codes = ["422", "503"] if path.startswith("/api/") else []
                if operation.get("security"):
                    codes += ["401", "403"]
                for code in codes:
                    operation["responses"][code] = {"description": "Standard API error",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}}
        return schema
    app.openapi = documented_openapi

    @app.middleware("http")
    async def identify_request(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail
        payload = detail if isinstance(detail, dict) else {"message": str(detail)}
        return JSONResponse(status_code=exc.status_code, headers=exc.headers,
                            content={"code": payload.get("code", f"http_{exc.status_code}"),
                                     "message": payload.get("message", "Request failed"),
                                     "request_id": getattr(request.state, "request_id", str(uuid4())),
                                     "details": payload.get("details", {})})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo passwords, tokens, or complete submitted payloads in errors.
        details = [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": "Invalid request",
                            "request_id": getattr(request.state, "request_id", str(uuid4())), "details": details})

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logging.getLogger("gastrogid").error("Unhandled error request_id=%s type=%s",
                                           getattr(request.state, "request_id", "unknown"), type(exc).__name__)
        return JSONResponse(status_code=500, content={"code": "internal_error", "message": "Internal service error",
                            "request_id": getattr(request.state, "request_id", str(uuid4())), "details": {}})
