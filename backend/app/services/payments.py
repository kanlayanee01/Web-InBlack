"""Payment webhooks: signature checking and the decision made for each event."""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from app.domain.state_machine import OrderStatus
from app.problem import Problem
from app.services import inventory, refunds
from app.services.transitions import lock_order, move, queue_fulfillment

SIGNATURE_TOLERANCE_SECONDS = 300
ACTOR_PROVIDER = "payment-provider"
S = OrderStatus


@dataclass(frozen=True)
class PaymentEvent:
    event_id: str
    type: str
    order_id: str
    amount_satang: int
    currency: str
    occurred_at: datetime


def verify_signature(raw_body: bytes, header: str | None, secret: str, now: int) -> None:
    rejected = Problem(401, "INVALID_SIGNATURE", "Webhook signature could not be verified")
    if header is None:
        raise rejected
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    timestamp, signature = parts.get("t", ""), parts.get("v1", "")
    if not timestamp.isascii() or not timestamp.isdigit():
        raise rejected
    if abs(now - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
        raise rejected
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256)
    if not hmac.compare_digest(expected.hexdigest().encode(), signature.encode()):
        raise rejected


async def _record_payment(conn: asyncpg.Connection, event: PaymentEvent) -> None:
    await conn.execute(
        """
        INSERT INTO ledger_entries (order_id, kind, amount_satang, provider_event_id)
        VALUES ($1, 'payment', $2, $3)
        """,
        event.order_id,
        event.amount_satang,
        event.event_id,
    )
    await conn.execute(
        "UPDATE orders SET paid_satang = paid_satang + $2 WHERE id = $1",
        event.order_id,
        event.amount_satang,
    )


async def _to_review(
    conn: asyncpg.Connection, order: asyncpg.Record, reason: str, event: PaymentEvent
) -> None:
    status = OrderStatus(order["status"])
    if status is not S.NEEDS_REVIEW:
        metadata = {"event_id": event.event_id, "amount_satang": event.amount_satang}
        await move(conn, order["id"], status, S.NEEDS_REVIEW, ACTOR_PROVIDER, reason, metadata)


async def _begin_fulfillment(
    conn: asyncpg.Connection, order_id: str, current: OrderStatus, reason: str
) -> None:
    await move(conn, order_id, current, S.PAID, ACTOR_PROVIDER, reason)
    await queue_fulfillment(conn, order_id, S.PAID, "system", "Queued for fulfillment")


async def _on_succeeded(
    conn: asyncpg.Connection, order: asyncpg.Record, event: PaymentEvent
) -> str:
    status = OrderStatus(order["status"])
    await _record_payment(conn, event)

    if order["paid_satang"] > 0:
        # A second capture under a new event id: real money that a person has to look at.
        await _to_review(conn, order, "Received a second payment for this order", event)
        return "duplicate_capture"

    if event.amount_satang != order["total_satang"] or event.currency != "THB":
        await _to_review(conn, order, "Paid amount does not match the order total", event)
        return "amount_mismatch"

    if status is S.PENDING_PAYMENT:
        await _begin_fulfillment(conn, order["id"], status, "Payment received")
        return "paid"

    if status is S.EXPIRED:
        if await inventory.reclaim_for_order(conn, order["id"], order["email"]):
            await _begin_fulfillment(conn, order["id"], status, "Late payment, inventory re-held")
            return "late_paid"
        await refunds.request_refund(
            conn,
            order["id"],
            event.amount_satang,
            "Late payment and the item is no longer available",
            "system:late-payment",
        )
        return "late_refunded"

    await _to_review(conn, order, f"Payment received while order was {status}", event)
    return "unexpected_payment"


async def _on_failed(conn: asyncpg.Connection, order: asyncpg.Record) -> str:
    status = OrderStatus(order["status"])
    if status is not S.PENDING_PAYMENT:
        # Covers payment.failed arriving after payment.succeeded: status never goes backwards.
        return "ignored_out_of_order"
    await move(conn, order["id"], status, S.PAYMENT_FAILED, ACTOR_PROVIDER, "Payment failed")
    await inventory.release_for_order(conn, order["id"], order["email"], release_voucher=True)
    return "payment_failed"


async def _on_refunded(
    conn: asyncpg.Connection, order: asyncpg.Record, event: PaymentEvent
) -> str:
    pending = await conn.fetchval(
        """
        SELECT id FROM refunds
        WHERE order_id = $1 AND status = 'PENDING' AND amount_satang = $2
        ORDER BY created_at
        LIMIT 1
        FOR UPDATE
        """,
        order["id"],
        event.amount_satang,
    )
    if pending is not None:
        await conn.execute(
            """
            UPDATE refunds SET status = 'SUCCEEDED', provider_event_id = $2, updated_at = now()
            WHERE id = $1
            """,
            pending,
            event.event_id,
        )
        await _record_refund(conn, event, pending)
        return "refund_confirmed"

    # A refund we did not start, for example one made from the provider's own dashboard.
    refunded_after = order["refunded_satang"] + event.amount_satang
    if refunded_after > order["paid_satang"]:
        await _to_review(conn, order, "Provider refunded more than the order balance", event)
        return "refund_exceeds_paid"
    refund_id = await conn.fetchval(
        """
        INSERT INTO refunds (order_id, amount_satang, status, reason, actor, provider_event_id)
        VALUES ($1, $2, 'SUCCEEDED', 'Refund initiated at the provider', $3, $4)
        RETURNING id
        """,
        order["id"],
        event.amount_satang,
        ACTOR_PROVIDER,
        event.event_id,
    )
    await _record_refund(conn, event, refund_id)
    await conn.execute(
        "UPDATE orders SET refunded_satang = $2 WHERE id = $1", order["id"], refunded_after
    )
    await refunds.settle_if_fully_refunded(
        conn, order, refunded_after, ACTOR_PROVIDER, "Refund initiated at the provider"
    )
    return "refund_external"


async def _record_refund(conn: asyncpg.Connection, event: PaymentEvent, refund_id: object) -> None:
    await conn.execute(
        """
        INSERT INTO ledger_entries (order_id, kind, amount_satang, provider_event_id, refund_id)
        VALUES ($1, 'refund', $2, $3, $4)
        """,
        event.order_id,
        event.amount_satang,
        event.event_id,
        refund_id,
    )


async def handle_event(pool: asyncpg.Pool, event: PaymentEvent, raw_body: str) -> str:
    async with pool.acquire() as conn, conn.transaction():
        # Simultaneous duplicates block here until the first commits, then see the conflict.
        # If processing fails, this row rolls back too, so the provider's retry is not ignored.
        first_delivery = await conn.fetchval(
            """
            INSERT INTO webhook_events (event_id, type, order_id, amount_satang, currency,
                                        occurred_at, raw_body)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            event.event_id,
            event.type,
            event.order_id,
            event.amount_satang,
            event.currency,
            event.occurred_at,
            raw_body,
        )
        if first_delivery is None:
            return "duplicate"

        order = await lock_order(conn, event.order_id)
        if order is None:
            outcome = "unknown_order"
        elif event.type == "payment.succeeded":
            outcome = await _on_succeeded(conn, order, event)
        elif event.type == "payment.failed":
            outcome = await _on_failed(conn, order)
        else:
            outcome = await _on_refunded(conn, order, event)

        await conn.execute(
            "UPDATE webhook_events SET outcome = $2 WHERE event_id = $1", event.event_id, outcome
        )
        return outcome
