"""Shared JWT verification; authorization remains local to each service."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import secrets
from uuid import uuid4

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    id: str
    role: str = "user"


def auth_secret() -> str:
    value = os.getenv("AUTH_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("AUTH_SECRET must contain at least 32 characters")
    return value


def create_access_token(principal: Principal, lifetime_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": principal.id, "role": principal.role, "iat": now,
                       "exp": now + timedelta(seconds=lifetime_seconds), "jti": str(uuid4()),
                       "iss": "gastrogid", "aud": "gastrogid-api"}, auth_secret(), algorithm="HS256")


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(credentials.credentials, auth_secret(), algorithms=["HS256"],
                            audience="gastrogid-api", issuer="gastrogid",
                            options={"require": ["sub", "role", "exp", "iat", "jti"]})
        if not isinstance(claims["sub"], str) or claims["role"] not in ("user", "admin"):
            raise jwt.InvalidTokenError("Invalid principal")
        return Principal(claims["sub"], claims["role"])
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})


def require_admin(principal: Principal = Depends(current_user)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(403, "Administrator access required")
    return principal


def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("INTERNAL_TOKEN", "")
    if len(expected) < 32 or not x_internal_token or not secrets.compare_digest(expected, x_internal_token):
        raise HTTPException(403, "Internal service access required")
