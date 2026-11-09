import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.admin_routes import router as admin_router
from app.api.customer_routes import router as customer_router
from app.api.routes import router
from app.domain.errors import DomainError
from app.problem import PROBLEM_CONTENT_TYPE, Problem, from_domain, problem_body
from app.services.ratelimit import RateLimiter
from app.services.realtime import OrderEventHub
from app.settings import get_settings

DEPENDENCY_TIMEOUT_SECONDS = 1


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # min_size=0 opens connections on demand, so the API can start, and recover, while Postgres
    # is down. /readyz reports the outage instead of the process crashing.
    app.state.db = await asyncpg.create_pool(settings.database_url, min_size=0, max_size=20)
    app.state.redis = redis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url,
        socket_connect_timeout=DEPENDENCY_TIMEOUT_SECONDS,
        socket_timeout=DEPENDENCY_TIMEOUT_SECONDS,
    )
    app.state.limiter = RateLimiter(app.state.redis)
    app.state.hub = OrderEventHub(settings.database_url)
    hub_task = asyncio.create_task(app.state.hub.run())
    try:
        yield
    finally:
        hub_task.cancel()
        await app.state.redis.aclose()
        await app.state.db.close()


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class ResponseHeaders:
    """Plain ASGI middleware, because BaseHTTPMiddleware buffers and interferes with SSE."""

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                headers["X-Served-By"] = get_settings().instance_id
            await send(message)

        await self.inner(scope, receive, with_headers)


app = FastAPI(title="InBlack Top-Up API", lifespan=lifespan)
app.add_middleware(ResponseHeaders)


app.include_router(router)
app.include_router(customer_router)
app.include_router(admin_router)


def _problem_response(problem: Problem) -> Response:
    return Response(
        problem_body(problem.status, problem.code, problem.detail),
        status_code=problem.status,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=problem.headers,
    )


@app.exception_handler(Problem)
async def handle_problem(_: Request, error: Problem) -> Response:
    return _problem_response(error)


@app.exception_handler(DomainError)
async def handle_domain_error(_: Request, error: DomainError) -> Response:
    return _problem_response(from_domain(error))


async def _handle_validation(_: Request, error: Exception) -> Response:
    assert isinstance(error, RequestValidationError | ValidationError)
    # Only field locations and messages: echoing input could leak PII into responses.
    fields = [
        {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
        for item in error.errors()
    ]
    return Response(
        problem_body(422, "VALIDATION_ERROR", "Request is not valid", errors=fields),
        status_code=422,
        media_type=PROBLEM_CONTENT_TYPE,
    )


app.add_exception_handler(RequestValidationError, _handle_validation)
app.add_exception_handler(ValidationError, _handle_validation)


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(_: Request, error: StarletteHTTPException) -> Response:
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(error.status_code, "HTTP_ERROR")
    return _problem_response(Problem(error.status_code, code, str(error.detail)))


@app.exception_handler(asyncpg.PostgresError)
async def handle_database_error(_: Request, error: asyncpg.PostgresError) -> Response:
    if error.sqlstate == "IB001":
        return _problem_response(Problem(409, "INVALID_TRANSITION", "Status change not allowed"))
    if isinstance(error, asyncpg.PostgresConnectionError | asyncpg.CannotConnectNowError):
        return _unavailable()
    return _problem_response(Problem(500, "INTERNAL_ERROR", "Something went wrong"))


def _unavailable() -> Response:
    return _problem_response(
        Problem(503, "SERVICE_UNAVAILABLE", "Please retry shortly", {"Retry-After": "1"})
    )


@app.exception_handler(OSError)
async def handle_connection_error(_: Request, __: OSError) -> Response:
    return _unavailable()


@app.exception_handler(asyncpg.InterfaceError)
async def handle_interface_error(_: Request, __: asyncpg.InterfaceError) -> Response:
    return _unavailable()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _database_ok(pool: asyncpg.Pool) -> bool:
    try:
        async with asyncio.timeout(DEPENDENCY_TIMEOUT_SECONDS):
            await pool.fetchval("SELECT 1")
        return True
    except (TimeoutError, OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
        return False


async def _redis_ok(client: redis.Redis) -> bool:
    try:
        async with asyncio.timeout(DEPENDENCY_TIMEOUT_SECONDS):
            await client.ping()
        return True
    except (TimeoutError, OSError, redis.RedisError):
        return False


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    database, cache = await asyncio.gather(
        _database_ok(request.app.state.db), _redis_ok(request.app.state.redis)
    )
    ready = database and cache
    status = "ready" if ready else "unavailable"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": status, "postgres": database, "redis": cache},
    )
