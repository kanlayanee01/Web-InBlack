"""Idempotency-Key handling.

The key row and the work it protects commit in one transaction. A simultaneous duplicate blocks on
the primary key until the first request finishes, then reads its stored response. If the first
request crashes, its transaction rolls back and the duplicate simply does the work instead.
"""

import hashlib
from collections.abc import Awaitable, Callable

import asyncpg

from app.domain.errors import DomainError
from app.problem import Problem, from_domain, problem_body

KEY_RETENTION_HOURS = 24
MAX_KEY_LENGTH = 255

Handler = Callable[[asyncpg.Connection], Awaitable[tuple[int, bytes]]]


def require_key(key: str | None) -> str:
    if key is None or not key.strip():
        raise Problem(400, "IDEMPOTENCY_KEY_REQUIRED", "The Idempotency-Key header is required")
    if len(key) > MAX_KEY_LENGTH or not key.isascii():
        raise Problem(400, "IDEMPOTENCY_KEY_INVALID", "The Idempotency-Key header is invalid")
    return key


async def run_once(
    pool: asyncpg.Pool, scope: str, key: str, raw_body: bytes, handler: Handler
) -> tuple[int, bytes]:
    request_hash = hashlib.sha256(raw_body).hexdigest()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "DELETE FROM idempotency_keys WHERE scope = $1 AND key = $2 AND expires_at <= now()",
            scope,
            key,
        )
        reserved = await conn.fetchval(
            """
            INSERT INTO idempotency_keys (scope, key, request_hash, state, expires_at)
            VALUES ($1, $2, $3, 'running', now() + make_interval(hours => $4))
            ON CONFLICT (scope, key) DO NOTHING
            RETURNING 1
            """,
            scope,
            key,
            request_hash,
            KEY_RETENTION_HOURS,
        )
        if reserved is None:
            stored = await conn.fetchrow(
                """
                SELECT request_hash, status_code, response_body FROM idempotency_keys
                WHERE scope = $1 AND key = $2
                """,
                scope,
                key,
            )
            if stored is None:
                raise Problem(503, "RETRY_LATER", "Please retry the request", {"Retry-After": "1"})
            if stored["request_hash"] != request_hash:
                raise Problem(
                    422, "IDEMPOTENCY_KEY_REUSED", "This key was used with a different body"
                )
            return stored["status_code"], bytes(stored["response_body"])

        try:
            async with conn.transaction():
                status, body = await handler(conn)
        except (DomainError, Problem) as error:
            # Business refusals are stored too. The savepoint has already undone partial work.
            problem = from_domain(error) if isinstance(error, DomainError) else error
            status = problem.status
            body = problem_body(problem.status, problem.code, problem.detail)

        await conn.execute(
            """
            UPDATE idempotency_keys
            SET state = 'completed', status_code = $3, response_body = $4
            WHERE scope = $1 AND key = $2
            """,
            scope,
            key,
            status,
            body,
        )
        return status, body
