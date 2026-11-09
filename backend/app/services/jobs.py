"""Job queue on PostgreSQL. A job status of 'dead' is the dead-letter queue.

A job is committed in the same transaction as the state change that requires it, so an order can
never be marked paid without its fulfillment job existing, even if Redis is down.
"""

import secrets

import asyncpg

LEASE_SECONDS = 15
# Crashes count as attempts. This extra allowance stops a job that kills the worker every time.
CRASH_ALLOWANCE = 3


def backoff_ms(attempt: int, base_ms: int, cap_ms: int) -> int:
    """Exponential backoff with equal jitter: half fixed, half random."""
    ceiling = min(cap_ms, base_ms << max(attempt - 1, 0))
    return ceiling // 2 + secrets.randbelow(ceiling // 2 + 1)


async def claim(pool: asyncpg.Pool, worker_id: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        UPDATE jobs SET status = 'running', locked_by = $1, attempts = attempts + 1,
                        lease_until = now() + make_interval(secs => $2::integer), updated_at = now()
        WHERE id = (
            SELECT id FROM jobs
            WHERE status = 'queued' AND run_at <= now()
            ORDER BY run_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING *
        """,
        worker_id,
        LEASE_SECONDS,
    )


async def extend_lease(pool: asyncpg.Pool, job_id: int, worker_id: str) -> None:
    await pool.execute(
        """
        UPDATE jobs SET lease_until = now() + make_interval(secs => $3::integer)
        WHERE id = $1 AND locked_by = $2 AND status = 'running'
        """,
        job_id,
        worker_id,
        LEASE_SECONDS,
    )


async def complete(pool: asyncpg.Pool, job_id: int, worker_id: str) -> None:
    await pool.execute(
        """
        UPDATE jobs SET status = 'done', lease_until = NULL, last_error = NULL, updated_at = now()
        WHERE id = $1 AND locked_by = $2 AND status = 'running'
        """,
        job_id,
        worker_id,
    )


async def retry_later(
    pool: asyncpg.Pool, job_id: int, worker_id: str, delay_ms: int, error: str
) -> None:
    await pool.execute(
        """
        UPDATE jobs SET status = 'queued', locked_by = NULL, lease_until = NULL,
                        run_at = now() + ($3::bigint * interval '1 millisecond'),
                        last_error = $4, updated_at = now()
        WHERE id = $1 AND locked_by = $2 AND status = 'running'
        """,
        job_id,
        worker_id,
        delay_ms,
        error,
    )


async def bury(pool: asyncpg.Pool, job_id: int, worker_id: str, error: str) -> None:
    await pool.execute(
        """
        UPDATE jobs SET status = 'dead', lease_until = NULL, last_error = $3, updated_at = now()
        WHERE id = $1 AND locked_by = $2 AND status = 'running'
        """,
        job_id,
        worker_id,
        error,
    )


async def reap_expired_leases(pool: asyncpg.Pool) -> None:
    """Hand back jobs whose worker died."""
    await pool.execute(
        """
        UPDATE jobs SET status = 'dead', lease_until = NULL, updated_at = now(),
                        last_error = 'worker died too many times while running this job'
        WHERE status = 'running' AND lease_until < now()
          AND attempts >= max_attempts + $1
        """,
        CRASH_ALLOWANCE,
    )
    await pool.execute(
        """
        UPDATE jobs SET status = 'queued', locked_by = NULL, lease_until = NULL,
                        run_at = now(), updated_at = now()
        WHERE status = 'running' AND lease_until < now()
        """
    )
