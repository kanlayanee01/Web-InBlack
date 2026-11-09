"""Quoting, creating and expiring orders."""

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.domain.errors import DomainError
from app.domain.order_number import generate_order_number
from app.domain.pricing import (
    Line,
    PaymentMethod,
    PriceBreakdown,
    VoucherKind,
    VoucherTerms,
    price_order,
)
from app.domain.state_machine import BADGE_BY_STATUS, OrderStatus
from app.services import inventory
from app.services.transitions import append_event, lock_order, move

ACTOR_CUSTOMER = "customer"
ACTOR_EXPIRY = "system:expiry"
EXPIRY_BATCH_SIZE = 200


@dataclass(frozen=True)
class RequestedItem:
    sku: str
    qty: int


@dataclass(frozen=True)
class NewOrder:
    items: list[RequestedItem]
    email: str
    game_uid: str
    payment_method: PaymentMethod
    voucher_code: str | None
    customer_id: UUID | None = None


@dataclass(frozen=True)
class Quote:
    products: list[asyncpg.Record]
    quantities: dict[str, int]
    voucher: asyncpg.Record | None
    price: PriceBreakdown


async def build_quote(
    conn: asyncpg.Connection,
    items: list[RequestedItem],
    method: PaymentMethod,
    voucher_code: str | None,
) -> Quote:
    quantities: dict[str, int] = {}
    for item in items:
        quantities[item.sku] = quantities.get(item.sku, 0) + item.qty

    rows = await conn.fetch(
        "SELECT * FROM products WHERE sku = ANY($1::text[]) AND is_active ORDER BY id",
        list(quantities),
    )
    if len(rows) != len(quantities):
        raise DomainError("PRODUCT_NOT_FOUND", "One or more products do not exist")
    for row in rows:
        limit = row["max_qty_per_order"]
        if limit is not None and quantities[row["sku"]] > limit:
            raise DomainError("PER_ORDER_LIMIT", f"{row['sku']} is limited to {limit} per order")

    voucher = None
    terms = None
    if voucher_code:
        voucher = await conn.fetchrow(
            "SELECT * FROM vouchers WHERE code = $1 AND is_active", voucher_code.strip().upper()
        )
        if voucher is None:
            raise DomainError("VOUCHER_NOT_FOUND", "This voucher does not exist")
        terms = VoucherTerms(
            code=voucher["code"],
            kind=VoucherKind(voucher["kind"]),
            min_subtotal_satang=voucher["min_subtotal_satang"],
            percent_bp=voucher["percent_bp"],
            max_discount_satang=voucher["max_discount_satang"],
            flat_satang=voucher["flat_satang"],
        )

    lines = [Line(row["price_satang"], quantities[row["sku"]]) for row in rows]
    return Quote(list(rows), quantities, voucher, price_order(lines, terms, method))


async def _insert_order_row(
    conn: asyncpg.Connection, new: NewOrder, quote: Quote, ttl_seconds: int
) -> str:
    # 13 random digits can collide; the primary key catches it and a new number is drawn.
    for _ in range(5):
        order_id = generate_order_number()
        inserted = await conn.fetchval(
            """
            INSERT INTO orders (id, customer_id, email, game_uid, payment_method, voucher_code,
                                subtotal_satang, discount_satang, net_satang, fee_satang,
                                total_satang, vat_satang, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    now() + make_interval(secs => $13::integer))
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """,
            order_id,
            new.customer_id,
            new.email,
            new.game_uid,
            new.payment_method,
            quote.voucher["code"] if quote.voucher else None,
            quote.price.subtotal_satang,
            quote.price.discount_satang,
            quote.price.net_satang,
            quote.price.fee_satang,
            quote.price.total_satang,
            quote.price.vat_satang,
            ttl_seconds,
        )
        if inserted is not None:
            return order_id
    raise RuntimeError("could not allocate an order number")


async def create_order(conn: asyncpg.Connection, new: NewOrder, ttl_seconds: int) -> str:
    """Must run inside a transaction: any refusal has to undo the whole order."""
    quote = await build_quote(conn, new.items, new.payment_method, new.voucher_code)
    order_id = await _insert_order_row(conn, new, quote, ttl_seconds)

    # Prices and names are copied, so later catalogue edits cannot touch this order.
    await conn.executemany(
        """
        INSERT INTO order_items (order_id, line_no, product_id, sku, name, unit_price_satang, qty)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        [
            (
                order_id,
                line_no,
                row["id"],
                row["sku"],
                row["name"],
                row["price_satang"],
                quote.quantities[row["sku"]],
            )
            for line_no, row in enumerate(quote.products, start=1)
        ],
    )
    await append_event(
        conn, order_id, None, OrderStatus.PENDING_PAYMENT, ACTOR_CUSTOMER, "Order created"
    )

    claims = [
        inventory.Claim(
            product_id=row["id"],
            qty=quote.quantities[row["sku"]],
            max_qty_per_email=row["max_qty_per_email"],
            tracks_stock=row["stock_total"] is not None,
        )
        for row in quote.products
    ]
    await inventory.claim_email_limit(conn, new.email, claims)
    if quote.voucher is not None:
        await inventory.claim_voucher(
            conn,
            order_id,
            quote.voucher["id"],
            new.email,
            quote.voucher["max_uses_per_email"] == 1,
        )
    await inventory.claim_stock(conn, claims)
    return order_id


async def fetch_order_json(
    conn: asyncpg.Connection, order_id: str, checkout_base_url: str
) -> dict[str, object] | None:
    order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
    if order is None:
        return None
    items = await conn.fetch(
        "SELECT sku, name, unit_price_satang, qty FROM order_items WHERE order_id = $1 "
        "ORDER BY line_no",
        order_id,
    )
    status = OrderStatus(order["status"])
    return {
        "id": order["id"],
        "status": status.value,
        "badge": BADGE_BY_STATUS[status].value,
        "email": order["email"],
        "game_uid": order["game_uid"],
        "payment_method": order["payment_method"],
        "voucher_code": order["voucher_code"],
        "items": [dict(item) for item in items],
        "subtotal_satang": order["subtotal_satang"],
        "discount_satang": order["discount_satang"],
        "net_satang": order["net_satang"],
        "fee_satang": order["fee_satang"],
        "total_satang": order["total_satang"],
        "vat_satang": order["vat_satang"],
        "currency": "THB",
        "expires_at": order["expires_at"].isoformat(),
        "created_at": order["created_at"].isoformat(),
        "checkout_url": (
            f"{checkout_base_url}/checkout/{order['id']}?amount={order['total_satang']}"
        ),
    }


async def expire_order(conn: asyncpg.Connection, order_id: str) -> bool:
    """Must run inside a transaction. Re-checks under the lock, so it cannot race a payment."""
    order = await lock_order(conn, order_id)
    if (
        order is None
        or order["status"] != OrderStatus.PENDING_PAYMENT
        or await conn.fetchval("SELECT $1::timestamptz > now()", order["expires_at"])
    ):
        return False
    await move(
        conn,
        order_id,
        OrderStatus.PENDING_PAYMENT,
        OrderStatus.EXPIRED,
        ACTOR_EXPIRY,
        "Payment window elapsed",
    )
    await inventory.release_for_order(conn, order_id, order["email"], release_voucher=True)
    return True


async def expire_due_orders(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        due = await conn.fetch(
            """
            SELECT id FROM orders
            WHERE status = 'PENDING_PAYMENT' AND expires_at <= now()
            ORDER BY expires_at
            LIMIT $1
            """,
            EXPIRY_BATCH_SIZE,
        )
        expired = 0
        for row in due:
            async with conn.transaction():
                expired += int(await expire_order(conn, row["id"]))
    return expired
