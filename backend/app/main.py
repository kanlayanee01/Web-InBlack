import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

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
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.db.close()


app = FastAPI(title="InBlack Top-Up API", lifespan=lifespan)


@app.middleware("http")
async def identify_replica(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
    response: Response = await call_next(request)
    response.headers["X-Served-By"] = get_settings().instance_id
    return response


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
