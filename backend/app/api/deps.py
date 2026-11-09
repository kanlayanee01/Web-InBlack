"""Session loading, CSRF protection and role checks."""

import hmac
from collections.abc import Awaitable, Callable

import asyncpg
from fastapi import Request, Response

from app.problem import Problem
from app.services.auth import hash_token

CUSTOMER_COOKIE = "ib_session"
ADMIN_COOKIE = "ib_admin"
ROLE_RANK = {"viewer": 1, "support": 2, "finance": 3, "superadmin": 4}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(request: Request) -> str:
    # Set by nginx, which is the only way to reach the API, so clients cannot forge it.
    return request.headers.get("x-real-ip", "unknown")


def set_session_cookie(
    response: Response, name: str, token: str, max_age: int, same_site: str
) -> None:
    response.set_cookie(
        name,
        token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite=same_site,  # type: ignore[arg-type]
        path="/",
    )


async def _load(request: Request, cookie: str, kind: str) -> asyncpg.Record | None:
    token = request.cookies.get(cookie)
    if token is None:
        return None
    row = await request.app.state.db.fetchrow(
        """
        SELECT s.csrf_token, s.customer_id, s.admin_id,
               c.email AS customer_email, a.email AS admin_email, a.role
        FROM sessions s
        LEFT JOIN customers c ON c.id = s.customer_id
        LEFT JOIN admin_users a ON a.id = s.admin_id AND a.is_active
        WHERE s.token_hash = $1 AND s.kind = $2 AND s.expires_at > now()
        """,
        hash_token(token),
        kind,
    )
    if row is None or (kind == "admin" and row["admin_email"] is None):
        return None
    return row


def _enforce_csrf(request: Request, session: asyncpg.Record) -> None:
    if request.method in _SAFE_METHODS:
        return
    supplied = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(supplied.encode(), session["csrf_token"].encode()):
        raise Problem(403, "CSRF_FAILED", "Missing or invalid CSRF token")


async def optional_customer(request: Request) -> asyncpg.Record | None:
    session = await _load(request, CUSTOMER_COOKIE, "customer")
    if session is not None:
        _enforce_csrf(request, session)
    return session


async def require_customer(request: Request) -> asyncpg.Record:
    session = await optional_customer(request)
    if session is None:
        raise Problem(401, "UNAUTHENTICATED", "Please sign in")
    return session


async def optional_admin(request: Request) -> asyncpg.Record | None:
    session = await _load(request, ADMIN_COOKIE, "admin")
    if session is not None:
        _enforce_csrf(request, session)
    return session


def require_role(minimum: str) -> Callable[[Request], Awaitable[asyncpg.Record]]:
    async def dependency(request: Request) -> asyncpg.Record:
        session = await optional_admin(request)
        if session is None:
            raise Problem(401, "UNAUTHENTICATED", "Please sign in")
        if ROLE_RANK[session["role"]] < ROLE_RANK[minimum]:
            raise Problem(403, "FORBIDDEN", "Your role cannot do this")
        return session

    return dependency
