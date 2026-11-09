"""Claims and releases of stock, per-email limits and vouchers.

Every claim is a single conditional statement, so the check and the write cannot be separated by
another request. Lock order is always email usage -> voucher -> stock, with products sorted by id,
which prevents deadlocks. Stock is last because it is the hottest row and is then held briefest.
"""

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.domain.errors import DomainError


@dataclass(frozen=True)
class Claim:
    product_id: UUID
    qty: int
    max_qty_per_email: int | None
    tracks_stock: bool


async def claim_email_limit(conn: asyncpg.Connection, email: str, claims: list[Claim]) -> None:
    for claim in claims:
        if claim.max_qty_per_email is None:
            continue
        accepted = None
        if claim.qty <= claim.max_qty_per_email:
            accepted = await conn.fetchval(
                """
                INSERT INTO product_email_usage AS usage (product_id, email, qty)
                VALUES ($1, $2, $3)
                ON CONFLICT (product_id, email) DO UPDATE SET qty = usage.qty + EXCLUDED.qty
                WHERE usage.qty + EXCLUDED.qty <= $4
                RETURNING qty
                """,
                claim.product_id,
                email,
                claim.qty,
                claim.max_qty_per_email,
            )
        if accepted is None:
            raise DomainError("PER_CUSTOMER_LIMIT", "Purchase limit per customer reached")


async def claim_stock(conn: asyncpg.Connection, claims: list[Claim]) -> None:
    for claim in claims:
        if not claim.tracks_stock:
            continue
        taken = await conn.fetchval(
            """
            UPDATE products SET stock_remaining = stock_remaining - $2
            WHERE id = $1 AND stock_remaining >= $2
            RETURNING id
            """,
            claim.product_id,
            claim.qty,
        )
        if taken is None:
            raise DomainError("OUT_OF_STOCK", "Not enough stock")


async def claim_voucher(
    conn: asyncpg.Connection,
    order_id: str,
    voucher_id: UUID,
    email: str,
    once_per_email: bool,
) -> None:
    created = await conn.fetchval(
        """
        INSERT INTO voucher_redemptions (voucher_id, order_id, email, once_per_email)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (voucher_id, email) WHERE released_at IS NULL AND once_per_email DO NOTHING
        RETURNING id
        """,
        voucher_id,
        order_id,
        email,
        once_per_email,
    )
    if created is None:
        raise DomainError("VOUCHER_ALREADY_USED", "This email has already used the voucher")
    await _take_voucher_use(conn, voucher_id)


async def _take_voucher_use(conn: asyncpg.Connection, voucher_id: UUID) -> None:
    taken = await conn.fetchval(
        """
        UPDATE vouchers SET used_count = used_count + 1
        WHERE id = $1 AND (max_total_uses IS NULL OR used_count < max_total_uses)
        RETURNING id
        """,
        voucher_id,
    )
    if taken is None:
        raise DomainError("VOUCHER_EXHAUSTED", "This voucher has been fully used")


async def _order_claims(conn: asyncpg.Connection, order_id: str) -> list[Claim]:
    rows = await conn.fetch(
        """
        SELECT i.product_id, i.qty, p.max_qty_per_email, p.stock_total IS NOT NULL AS tracks_stock
        FROM order_items i JOIN products p ON p.id = i.product_id
        WHERE i.order_id = $1
        ORDER BY i.product_id
        """,
        order_id,
    )
    return [Claim(**dict(row)) for row in rows]


async def reclaim_for_order(conn: asyncpg.Connection, order_id: str, email: str) -> bool:
    """Claim everything again for an order that gave its holds back. All or nothing."""
    try:
        async with conn.transaction():
            claims = await _order_claims(conn, order_id)
            await claim_email_limit(conn, email, claims)
            voucher_id = await conn.fetchval(
                """
                UPDATE voucher_redemptions SET released_at = NULL
                WHERE order_id = $1 AND released_at IS NOT NULL
                RETURNING voucher_id
                """,
                order_id,
            )
            if voucher_id is not None:
                await _take_voucher_use(conn, voucher_id)
            await claim_stock(conn, claims)
            await conn.execute("UPDATE orders SET holds_inventory = true WHERE id = $1", order_id)
    except (DomainError, asyncpg.UniqueViolationError):
        # UniqueViolation: the same email redeemed this voucher on another order in the meantime.
        return False
    return True


async def release_for_order(
    conn: asyncpg.Connection, order_id: str, email: str, *, release_voucher: bool
) -> None:
    """Give back what an order holds. Safe to call twice: holds_inventory guards it."""
    held = await conn.fetchval(
        """
        UPDATE orders SET holds_inventory = false
        WHERE id = $1 AND holds_inventory
        RETURNING id
        """,
        order_id,
    )
    if held is None:
        return
    claims = await _order_claims(conn, order_id)
    for claim in claims:
        if claim.max_qty_per_email is not None:
            await conn.execute(
                """
                UPDATE product_email_usage SET qty = GREATEST(qty - $3, 0)
                WHERE product_id = $1 AND email = $2
                """,
                claim.product_id,
                email,
                claim.qty,
            )
    if release_voucher:
        voucher_id = await conn.fetchval(
            """
            UPDATE voucher_redemptions SET released_at = now()
            WHERE order_id = $1 AND released_at IS NULL
            RETURNING voucher_id
            """,
            order_id,
        )
        if voucher_id is not None:
            await conn.execute(
                "UPDATE vouchers SET used_count = used_count - 1 WHERE id = $1", voucher_id
            )
    # Goods that reached the player, or may have, must never go back on sale.
    delivered = await conn.fetchval(
        """
        SELECT EXISTS (SELECT 1 FROM fulfillments
                       WHERE order_id = $1 AND status IN ('SUCCEEDED','SUBMITTING','UNKNOWN'))
        """,
        order_id,
    )
    if not delivered:
        for claim in claims:
            if claim.tracks_stock:
                await conn.execute(
                    "UPDATE products SET stock_remaining = stock_remaining + $2 WHERE id = $1",
                    claim.product_id,
                    claim.qty,
                )
