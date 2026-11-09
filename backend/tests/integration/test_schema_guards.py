"""Proves the database itself refuses bad data, using a real PostgreSQL.

Each test runs inside a transaction that is rolled back, so the seeded data is left untouched.
"""

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from app.domain.state_machine import ALLOWED_TRANSITIONS, OrderStatus

ORDER_ID = "ORD-1000000000000-030"


@pytest.fixture
async def db() -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(os.environ["DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


async def insert_order(db: asyncpg.Connection, status: str = "PENDING_PAYMENT") -> None:
    await db.execute(
        """
        INSERT INTO orders (id, email, game_uid, payment_method, subtotal_satang, discount_satang,
                            net_satang, fee_satang, total_satang, vat_satang, status, expires_at)
        VALUES ($1, 'a@example.com', '12345', 'promptpay', 10700, 0, 10700, 0, 10700, 700, $2,
                now() + interval '15 minutes')
        """,
        ORDER_ID,
        status,
    )


async def test_database_transition_table_matches_the_domain(db):
    rows = await db.fetch("SELECT from_status, to_status FROM order_status_transitions")
    assert {(r["from_status"], r["to_status"]) for r in rows} == {
        (a.value, b.value) for a, b in ALLOWED_TRANSITIONS
    }


async def test_every_status_pair_is_enforced_by_the_trigger(db):
    await insert_order(db)
    for current in OrderStatus:
        for target in OrderStatus:
            if current is target:
                continue
            savepoint = db.transaction()
            await savepoint.start()
            try:
                await db.execute(
                    "ALTER TABLE orders DISABLE TRIGGER orders_guard_transition_trg"
                )
                await db.execute("UPDATE orders SET status = $1 WHERE id = $2", current, ORDER_ID)
                await db.execute("ALTER TABLE orders ENABLE TRIGGER orders_guard_transition_trg")
                if (current, target) in ALLOWED_TRANSITIONS:
                    await db.execute(
                        "UPDATE orders SET status = $1 WHERE id = $2", target, ORDER_ID
                    )
                else:
                    with pytest.raises(asyncpg.PostgresError) as caught:
                        await db.execute(
                            "UPDATE orders SET status = $1 WHERE id = $2", target, ORDER_ID
                        )
                    assert caught.value.sqlstate == "IB001"
            finally:
                await savepoint.rollback()


async def test_flash_stock_cannot_go_negative(db):
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "UPDATE products SET stock_remaining = stock_remaining - 101 "
            "WHERE sku = 'FLASH-SKIN-01'"
        )


async def test_refund_cannot_exceed_amount_paid(db):
    await insert_order(db, "COMPLETED")
    await db.execute("UPDATE orders SET paid_satang = 10700 WHERE id = $1", ORDER_ID)
    await db.execute("UPDATE orders SET refunded_satang = 10700 WHERE id = $1", ORDER_ID)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE orders SET refunded_satang = 10701 WHERE id = $1", ORDER_ID)


async def test_voucher_total_cap_is_enforced(db):
    await db.execute("UPDATE vouchers SET used_count = 10 WHERE code = 'SAVE10'")
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE vouchers SET used_count = 11 WHERE code = 'SAVE10'")


async def _redeem(db: asyncpg.Connection, voucher_id: object, order_id: str) -> None:
    await db.execute(
        """
        INSERT INTO voucher_redemptions (voucher_id, order_id, email, once_per_email)
        VALUES ($1, $2, 'a@example.com', true)
        """,
        voucher_id,
        order_id,
    )


async def test_voucher_is_single_use_per_email_until_released(db):
    await insert_order(db)
    second = "ORD-1000000000001-031"
    await db.execute(
        """
        INSERT INTO orders (id, email, game_uid, payment_method, subtotal_satang, discount_satang,
                            net_satang, fee_satang, total_satang, vat_satang, expires_at)
        SELECT $1, email, game_uid, payment_method, subtotal_satang, discount_satang, net_satang,
               fee_satang, total_satang, vat_satang, expires_at
        FROM orders WHERE id = $2
        """,
        second,
        ORDER_ID,
    )
    voucher_id = await db.fetchval("SELECT id FROM vouchers WHERE code = 'SAVE10'")
    await _redeem(db, voucher_id, ORDER_ID)

    savepoint = db.transaction()
    await savepoint.start()
    with pytest.raises(asyncpg.UniqueViolationError):
        await _redeem(db, voucher_id, second)
    await savepoint.rollback()

    await db.execute(
        "UPDATE voucher_redemptions SET released_at = now() WHERE order_id = $1", ORDER_ID
    )
    await _redeem(db, voucher_id, second)


@pytest.mark.parametrize(
    "statement", ["UPDATE order_events SET reason = 'x'", "DELETE FROM order_events"]
)
async def test_order_events_are_append_only(db, statement):
    await insert_order(db)
    await db.execute(
        """
        INSERT INTO order_events (order_id, seq, to_status, actor, reason)
        VALUES ($1, 1, 'PENDING_PAYMENT', 'system', 'created')
        """,
        ORDER_ID,
    )
    with pytest.raises(asyncpg.PostgresError) as caught:
        await db.execute(statement)
    assert caught.value.sqlstate == "IB002"


async def test_same_webhook_event_cannot_be_stored_twice(db):
    insert = """
        INSERT INTO webhook_events (event_id, type, amount_satang, currency, occurred_at, raw_body)
        VALUES ('evt_1', 'payment.succeeded', 100, 'THB', now(), '{}')
    """
    await db.execute(insert)
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(insert)


async def test_order_arithmetic_is_checked(db):
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            """
            INSERT INTO orders (id, email, game_uid, payment_method, subtotal_satang,
                                discount_satang, net_satang, fee_satang, total_satang, vat_satang,
                                expires_at)
            VALUES ($1, 'a@example.com', '1', 'card', 100, 0, 100, 0, 999, 0, now())
            """,
            ORDER_ID,
        )


async def test_seed_data_matches_the_spec(db):
    skin = await db.fetchrow("SELECT * FROM products WHERE sku = 'FLASH-SKIN-01'")
    assert (skin["price_satang"], skin["stock_total"], skin["max_qty_per_email"]) == (9900, 100, 2)
    assert await db.fetchval(
        "SELECT max_qty_per_order FROM products WHERE sku = 'MONTHLY-PASS'"
    ) == 1
    save10 = await db.fetchrow("SELECT * FROM vouchers WHERE code = 'SAVE10'")
    assert (save10["percent_bp"], save10["max_discount_satang"]) == (1000, 5000)
    assert (save10["max_total_uses"], save10["max_uses_per_email"]) == (10, 1)
    roles = await db.fetch("SELECT role FROM admin_users")
    assert {r["role"] for r in roles} == {"viewer", "support", "finance", "superadmin"}
