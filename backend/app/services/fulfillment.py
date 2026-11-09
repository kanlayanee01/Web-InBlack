"""Game top-ups without ever topping up twice.

The downstream system has no idempotency, so the guarantee comes from this order of steps:

1. Commit status SUBMITTING *before* sending the POST.
2. A line that is not PENDING may already have been sent, so it is looked up with GET first.
3. POST again only when GET positively says it did not apply. If GET cannot answer, do not POST.

Whichever instant the worker dies at, the next attempt finds either PENDING (nothing was sent) or
SUBMITTING (something may have been sent, so step 2 applies).
"""

from dataclasses import dataclass
from enum import Enum, auto

import asyncpg
import httpx

from app.domain.state_machine import OrderStatus
from app.services import inventory
from app.services.transitions import lock_order, move

ACTOR = "system:worker"
S = OrderStatus


class Verdict(Enum):
    FINISHED = auto()
    RETRY = auto()
    EXHAUSTED = auto()


class Lookup(Enum):
    APPLIED = auto()
    NOT_APPLIED = auto()
    PROCESSING = auto()
    UNREACHABLE = auto()


class LineState(Enum):
    APPLIED = auto()
    NOT_APPLIED = auto()
    UNCERTAIN = auto()


@dataclass(frozen=True)
class Answer:
    outcome: Lookup
    http_status: int | None
    detail: str


class GameClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def lookup(self, reference: str) -> Answer:
        try:
            response = await self._client.get(f"/topups/{reference}")
        except httpx.HTTPError as error:
            return Answer(Lookup.UNREACHABLE, None, type(error).__name__)
        if response.status_code == 404:
            return Answer(Lookup.NOT_APPLIED, 404, "unknown reference")
        if not response.is_success:
            return Answer(Lookup.UNREACHABLE, response.status_code, "lookup failed")
        try:
            status = response.json()["status"]
        except (ValueError, KeyError, TypeError):
            return Answer(Lookup.UNREACHABLE, response.status_code, "unreadable response")
        outcome = {
            "SUCCEEDED": Lookup.APPLIED,
            "FAILED": Lookup.NOT_APPLIED,
            "PROCESSING": Lookup.PROCESSING,
        }.get(status, Lookup.UNREACHABLE)
        return Answer(outcome, response.status_code, str(status))

    async def submit(self, reference: str, game_uid: str, sku: str, qty: int) -> Answer:
        try:
            response = await self._client.post(
                "/topups",
                json={"reference": reference, "game_uid": game_uid, "sku": sku, "qty": qty},
            )
        except httpx.HTTPError as error:
            return Answer(Lookup.UNREACHABLE, None, type(error).__name__)
        if response.is_success:
            return Answer(Lookup.APPLIED, response.status_code, "ok")
        return Answer(Lookup.UNREACHABLE, response.status_code, "submit failed")


async def _log_attempt(
    pool: asyncpg.Pool, line: asyncpg.Record, action: str, answer: Answer
) -> None:
    await pool.execute(
        """
        INSERT INTO fulfillment_attempts (fulfillment_id, order_id, action, outcome, http_status,
                                          detail)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        line["id"],
        line["order_id"],
        action,
        answer.outcome.name,
        answer.http_status,
        answer.detail,
    )


async def _mark_succeeded(pool: asyncpg.Pool, line: asyncpg.Record) -> None:
    await pool.execute(
        """
        UPDATE fulfillments SET status = 'SUCCEEDED', confirmed_at = now(), last_error = NULL,
                                updated_at = now()
        WHERE id = $1
        """,
        line["id"],
    )


async def _advance_line(
    pool: asyncpg.Pool, game: GameClient, line: asyncpg.Record, final: bool
) -> tuple[LineState, str]:
    if line["status"] != "PENDING":
        found = await game.lookup(line["reference"])
        await _log_attempt(pool, line, "GET", found)
        if found.outcome is Lookup.APPLIED:
            await _mark_succeeded(pool, line)
            return LineState.APPLIED, ""
        if found.outcome is not Lookup.NOT_APPLIED:
            return LineState.UNCERTAIN, f"lookup: {found.detail}"

    if final:
        return LineState.NOT_APPLIED, "retries exhausted"

    # Write-ahead: this commit must happen before the request leaves.
    await pool.execute(
        """
        UPDATE fulfillments SET status = 'SUBMITTING', attempts = attempts + 1,
                                submitted_at = now(), updated_at = now()
        WHERE id = $1
        """,
        line["id"],
    )
    sent = await game.submit(line["reference"], line["game_uid"], line["sku"], line["qty"])
    await _log_attempt(pool, line, "POST", sent)
    if sent.outcome is Lookup.APPLIED:
        await _mark_succeeded(pool, line)
        return LineState.APPLIED, ""
    # Status stays SUBMITTING on purpose: the next attempt must ask before sending again.
    await pool.execute(
        "UPDATE fulfillments SET last_error = $2, updated_at = now() WHERE id = $1",
        line["id"],
        sent.detail,
    )
    return LineState.UNCERTAIN, f"submit: {sent.detail}"


async def _start(pool: asyncpg.Pool, order_id: str) -> bool:
    async with pool.acquire() as conn, conn.transaction():
        order = await lock_order(conn, order_id)
        if order is None:
            return False
        if order["status"] == S.IN_QUEUE:
            await move(conn, order_id, S.IN_QUEUE, S.IN_PROGRESS, ACTOR, "Top-up started")
            return True
        return bool(order["status"] == S.IN_PROGRESS)


async def _complete(pool: asyncpg.Pool, order_id: str) -> None:
    async with pool.acquire() as conn, conn.transaction():
        order = await lock_order(conn, order_id)
        # Anything else means a person took over (NEEDS_REVIEW), so the order is left alone.
        if order is not None and order["status"] == S.IN_PROGRESS:
            await move(conn, order_id, S.IN_PROGRESS, S.COMPLETED, ACTOR, "Top-up delivered")


async def _conclude(pool: asyncpg.Pool, order_id: str, states: dict[object, LineState]) -> str:
    values = set(states.values())
    certainly_nothing_delivered = values == {LineState.NOT_APPLIED}
    async with pool.acquire() as conn, conn.transaction():
        order = await lock_order(conn, order_id)
        if order is None:
            return "order disappeared"
        for line_id, state in states.items():
            if state is not LineState.APPLIED:
                failed = state is LineState.NOT_APPLIED
                await conn.execute(
                    "UPDATE fulfillments SET status = $2, updated_at = now() WHERE id = $1",
                    line_id,
                    "FAILED" if failed else "UNKNOWN",
                )
        if order["status"] != S.IN_PROGRESS:
            return "order left IN_PROGRESS while retrying"
        if certainly_nothing_delivered:
            await move(conn, order_id, S.IN_PROGRESS, S.FAILED, ACTOR, "Top-up retries exhausted")
            await inventory.release_for_order(conn, order_id, order["email"], release_voucher=False)
            return "retries exhausted, nothing was delivered"
        await move(
            conn,
            order_id,
            S.IN_PROGRESS,
            S.NEEDS_REVIEW,
            ACTOR,
            "Top-up result is uncertain or only partly delivered",
        )
        return "uncertain or partial delivery, needs a person"


async def _run(
    pool: asyncpg.Pool, game: GameClient, order_id: str, final: bool
) -> tuple[Verdict, str]:
    if not await _start(pool, order_id):
        return Verdict.FINISHED, ""
    lines = await pool.fetch(
        """
        SELECT f.id, f.order_id, f.reference, f.status, i.sku, i.qty, o.game_uid
        FROM fulfillments f
        JOIN order_items i ON i.id = f.order_item_id
        JOIN orders o ON o.id = f.order_id
        WHERE f.order_id = $1
        ORDER BY i.line_no
        """,
        order_id,
    )
    states: dict[object, LineState] = {}
    for line in lines:
        if line["status"] == "SUCCEEDED":
            states[line["id"]] = LineState.APPLIED
            continue
        state, error = await _advance_line(pool, game, line, final)
        states[line["id"]] = state
        if state is not LineState.APPLIED and not final:
            return Verdict.RETRY, error

    if all(state is LineState.APPLIED for state in states.values()):
        await _complete(pool, order_id)
        return Verdict.FINISHED, ""
    return Verdict.EXHAUSTED, await _conclude(pool, order_id, states)


async def fulfill_order(
    pool: asyncpg.Pool, game: GameClient, order_id: str, final: bool
) -> tuple[Verdict, str]:
    async with pool.acquire() as guard:
        # Held by this connection, so Postgres frees it automatically if the worker dies.
        # Returning the connection to the pool also releases it (asyncpg's reset query).
        locked = await guard.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", order_id
        )
        if not locked:
            return Verdict.RETRY, "order is being processed elsewhere"
        return await _run(pool, game, order_id, final)


async def flag_stuck_orders(pool: asyncpg.Pool) -> int:
    """Safety net: an order IN_PROGRESS with no live job would otherwise wait forever."""
    rows = await pool.fetch(
        """
        SELECT o.id FROM orders o
        WHERE o.status = 'IN_PROGRESS'
          AND NOT EXISTS (
              SELECT 1 FROM jobs j
              WHERE j.dedupe_key = 'fulfill:' || o.id AND j.status IN ('queued','running')
          )
        """
    )
    flagged = 0
    for row in rows:
        async with pool.acquire() as conn, conn.transaction():
            order = await lock_order(conn, row["id"])
            still_orphaned = await conn.fetchval(
                """
                SELECT NOT EXISTS (SELECT 1 FROM jobs WHERE dedupe_key = $1
                                   AND status IN ('queued','running'))
                """,
                f"fulfill:{row['id']}",
            )
            if order is not None and order["status"] == S.IN_PROGRESS and still_orphaned:
                await move(
                    conn, row["id"], S.IN_PROGRESS, S.NEEDS_REVIEW, ACTOR, "Fulfillment job lost"
                )
                flagged += 1
    return flagged
