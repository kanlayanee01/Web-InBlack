"""The only place that changes an order's status. Callers must hold the order row lock."""

import json
from collections.abc import Mapping

import asyncpg

from app.domain.state_machine import OrderStatus, assert_transition


async def lock_order(conn: asyncpg.Connection, order_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow("SELECT * FROM orders WHERE id = $1 FOR UPDATE", order_id)


async def append_event(
    conn: asyncpg.Connection,
    order_id: str,
    from_status: OrderStatus | None,
    to_status: OrderStatus,
    actor: str,
    reason: str,
    metadata: Mapping[str, object] | None = None,
) -> None:
    await conn.execute(
        """
        WITH bumped AS (
            UPDATE orders SET event_seq = event_seq + 1 WHERE id = $1 RETURNING event_seq
        )
        INSERT INTO order_events (order_id, seq, from_status, to_status, actor, reason, metadata)
        SELECT $1, event_seq, $2, $3, $4, $5, $6::jsonb FROM bumped
        """,
        order_id,
        from_status,
        to_status,
        actor,
        reason,
        json.dumps(metadata or {}),
    )


async def move(
    conn: asyncpg.Connection,
    order_id: str,
    current: OrderStatus,
    target: OrderStatus,
    actor: str,
    reason: str,
    metadata: Mapping[str, object] | None = None,
) -> None:
    assert_transition(current, target)
    await conn.execute("UPDATE orders SET status = $1 WHERE id = $2", target, order_id)
    await append_event(conn, order_id, current, target, actor, reason, metadata)


async def enqueue_job(
    conn: asyncpg.Connection,
    kind: str,
    payload: Mapping[str, object],
    dedupe_key: str,
    max_attempts: int = 6,
) -> None:
    await conn.execute(
        """
        INSERT INTO jobs (kind, payload, dedupe_key, max_attempts)
        VALUES ($1, $2::jsonb, $3, $4)
        ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running')
        DO NOTHING
        """,
        kind,
        json.dumps(payload),
        dedupe_key,
        max_attempts,
    )


async def queue_fulfillment(
    conn: asyncpg.Connection, order_id: str, current: OrderStatus, actor: str, reason: str
) -> None:
    await move(conn, order_id, current, OrderStatus.IN_QUEUE, actor, reason)
    # The reference is fixed per order line and never regenerated, including on manual retries.
    await conn.execute(
        """
        INSERT INTO fulfillments (order_item_id, order_id, reference)
        SELECT id, order_id, order_id || '-' || line_no FROM order_items WHERE order_id = $1
        ON CONFLICT (order_item_id) DO NOTHING
        """,
        order_id,
    )
    await enqueue_job(conn, "fulfill_order", {"order_id": order_id}, f"fulfill:{order_id}")
