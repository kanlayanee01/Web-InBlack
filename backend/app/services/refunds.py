"""Refunds. The amount is counted against the order the moment a refund is accepted.

Two finance users racing therefore serialize on the order row, and the second one sees the first
one's amount. The provider call happens afterwards, in a job.
"""

from uuid import UUID

import asyncpg

from app.domain.errors import DomainError
from app.domain.state_machine import OrderStatus, can_transition
from app.services import inventory
from app.services.transitions import enqueue_job, lock_order, move

REFUNDABLE = {
    OrderStatus.COMPLETED,
    OrderStatus.FAILED,
    OrderStatus.NEEDS_REVIEW,
    OrderStatus.EXPIRED,
}


async def settle_if_fully_refunded(
    conn: asyncpg.Connection, order: asyncpg.Record, refunded_after: int, actor: str, reason: str
) -> None:
    if refunded_after != order["paid_satang"]:
        return
    status = OrderStatus(order["status"])
    if status is OrderStatus.REFUNDED:
        return
    if not can_transition(status, OrderStatus.REFUNDED):
        await move(conn, order["id"], status, OrderStatus.NEEDS_REVIEW, actor, reason)
        return
    await move(conn, order["id"], status, OrderStatus.REFUNDED, actor, reason)
    await inventory.release_for_order(conn, order["id"], order["email"], release_voucher=False)


async def request_refund(
    conn: asyncpg.Connection, order_id: str, amount_satang: int, reason: str, actor: str
) -> UUID:
    """Must run inside a transaction."""
    order = await lock_order(conn, order_id)
    if order is None:
        raise DomainError("ORDER_NOT_FOUND", "Order not found")
    if OrderStatus(order["status"]) not in REFUNDABLE:
        raise DomainError("ORDER_NOT_REFUNDABLE", f"Cannot refund an order in {order['status']}")
    if amount_satang <= 0:
        raise DomainError("INVALID_REFUND_AMOUNT", "Refund amount must be positive")
    refunded_after = order["refunded_satang"] + amount_satang
    if refunded_after > order["paid_satang"]:
        raise DomainError("REFUND_EXCEEDS_PAID", "Refund is larger than the refundable balance")

    refund_id: UUID = await conn.fetchval(
        """
        INSERT INTO refunds (order_id, amount_satang, status, reason, actor)
        VALUES ($1, $2, 'PENDING', $3, $4)
        RETURNING id
        """,
        order_id,
        amount_satang,
        reason,
        actor,
    )
    await conn.execute(
        "UPDATE orders SET refunded_satang = $2 WHERE id = $1", order_id, refunded_after
    )
    await settle_if_fully_refunded(conn, order, refunded_after, actor, reason)
    await enqueue_job(
        conn,
        "refund_provider",
        {"refund_id": str(refund_id), "order_id": order_id},
        f"refund:{refund_id}",
        max_attempts=8,
    )
    return refund_id
