"""Spec 5.4 invariants 1-4, proved with genuinely simultaneous requests."""

import asyncio
import uuid
from collections import Counter

from app.services import refunds
from tests.api.conftest import order_body, post_order, signed, webhook_bytes

FLASH = "FLASH-SKIN-01"


async def stock(db) -> int:
    return await db.fetchval("SELECT stock_remaining FROM products WHERE sku = $1", FLASH)


async def test_flash_sale_never_oversells_with_1500_simultaneous_buyers(client, db):
    bodies = [order_body([(FLASH, 1)]) for _ in range(1500)]
    responses = await asyncio.gather(*(post_order(client, body) for body in bodies))

    codes = Counter(r.status_code for r in responses)
    assert codes[201] == 100, codes
    assert codes[409] == 1400, codes
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {"OUT_OF_STOCK"}
    assert await stock(db) == 0
    assert await db.fetchval(
        "SELECT sum(qty) FROM order_items WHERE sku = $1", FLASH
    ) == 100


async def test_multi_quantity_buyers_cannot_oversell(client, db):
    bodies = [order_body([(FLASH, 2)]) for _ in range(120)]
    responses = await asyncio.gather(*(post_order(client, body) for body in bodies))
    assert Counter(r.status_code for r in responses)[201] == 50
    assert await stock(db) == 0


async def test_per_email_limit_holds_under_concurrency(client, db):
    email = "same.buyer@example.com"
    bodies = [order_body([(FLASH, 1)], email=email) for _ in range(30)]
    responses = await asyncio.gather(*(post_order(client, body) for body in bodies))

    codes = Counter(r.status_code for r in responses)
    assert codes[201] == 2, codes
    assert {r.json()["code"] for r in responses if r.status_code != 201} == {"PER_CUSTOMER_LIMIT"}
    assert await stock(db) == 98


async def test_voucher_total_cap_holds_under_concurrency(client, db):
    bodies = [order_body([("DIAMOND-1000", 1)], voucher="SAVE10") for _ in range(60)]
    responses = await asyncio.gather(*(post_order(client, body) for body in bodies))

    codes = Counter(r.status_code for r in responses)
    assert codes[201] == 10, codes
    assert {r.json()["code"] for r in responses if r.status_code != 201} == {"VOUCHER_EXHAUSTED"}
    assert await db.fetchval("SELECT used_count FROM vouchers WHERE code = 'SAVE10'") == 10


async def test_voucher_is_used_once_per_email_under_concurrency(client, db):
    email = "voucher.buyer@example.com"
    bodies = [order_body([("DIAMOND-1000", 1)], email=email, voucher="SAVE10") for _ in range(25)]
    responses = await asyncio.gather(*(post_order(client, body) for body in bodies))

    codes = Counter(r.status_code for r in responses)
    assert codes[201] == 1, codes
    assert {r.json()["code"] for r in responses if r.status_code != 201} == {
        "VOUCHER_ALREADY_USED"
    }
    assert await db.fetchval("SELECT used_count FROM vouchers WHERE code = 'SAVE10'") == 1


async def test_refused_orders_leave_no_side_effects(client, db):
    await db.execute("UPDATE products SET stock_remaining = 0 WHERE sku = $1", FLASH)
    response = await post_order(
        client, order_body([(FLASH, 2)], email="x@example.com", voucher="FLAT20")
    )
    assert response.status_code == 409
    assert await db.fetchval("SELECT count(*) FROM orders") == 0
    assert await db.fetchval("SELECT used_count FROM vouchers WHERE code = 'FLAT20'") == 0
    assert await db.fetchval("SELECT count(*) FROM product_email_usage WHERE qty > 0") == 0


async def test_same_idempotency_key_20_times_is_one_order_with_identical_bytes(client, db):
    key = uuid.uuid4().hex
    body = order_body([(FLASH, 2)], voucher="FLAT20")
    responses = await asyncio.gather(
        *(
            client.post("/api/v1/orders", json=body, headers={"Idempotency-Key": key})
            for _ in range(20)
        )
    )
    assert {r.status_code for r in responses} == {201}
    assert len({r.content for r in responses}) == 1
    assert await db.fetchval("SELECT count(*) FROM orders") == 1
    assert await stock(db) == 98
    assert await db.fetchval("SELECT used_count FROM vouchers WHERE code = 'FLAT20'") == 1


async def test_same_webhook_20_times_simultaneously_applies_once(client, db):
    created = (await post_order(client, order_body([("DIAMOND-1000", 1)]))).json()
    raw = webhook_bytes(created["id"], created["total_satang"], event_id="evt_same_1")
    responses = await asyncio.gather(
        *(
            client.post("/api/v1/webhooks/payment", content=raw, headers=signed(raw))
            for _ in range(20)
        )
    )

    assert {r.status_code for r in responses} == {200}
    outcomes = Counter(r.json()["outcome"] for r in responses)
    assert outcomes == {"paid": 1, "duplicate": 19}
    assert await db.fetchval("SELECT count(*) FROM ledger_entries") == 1
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE kind = 'fulfill_order'") == 1
    order = await db.fetchrow("SELECT * FROM orders WHERE id = $1", created["id"])
    assert (order["status"], order["paid_satang"]) == ("IN_QUEUE", created["total_satang"])
    seqs = await db.fetch(
        "SELECT seq, to_status FROM order_events WHERE order_id = $1 ORDER BY seq", created["id"]
    )
    assert [(r["seq"], r["to_status"]) for r in seqs] == [
        (1, "PENDING_PAYMENT"),
        (2, "PAID"),
        (3, "IN_QUEUE"),
    ]


async def _completed_order(client, db, send_webhook, sku: str = "DIAMOND-1000") -> dict:
    created = (await post_order(client, order_body([(sku, 1)]))).json()
    await send_webhook(created["id"], created["total_satang"])
    await db.execute("UPDATE orders SET status = 'IN_PROGRESS' WHERE id = $1", created["id"])
    await db.execute("UPDATE orders SET status = 'COMPLETED' WHERE id = $1", created["id"])
    return created


async def _try_refund(db, order_id: str, amount: int) -> str:
    async with db.acquire() as conn:
        try:
            async with conn.transaction():
                await refunds.request_refund(conn, order_id, amount, "test", "finance@test")
            return "ok"
        except refunds.DomainError as error:
            return error.code


async def test_two_full_refunds_at_once_only_one_succeeds(client, db, send_webhook):
    order = await _completed_order(client, db, send_webhook)
    results = await asyncio.gather(
        _try_refund(db, order["id"], order["total_satang"]),
        _try_refund(db, order["id"], order["total_satang"]),
    )
    assert sorted(results) in (["ORDER_NOT_REFUNDABLE", "ok"], ["REFUND_EXCEEDS_PAID", "ok"])
    row = await db.fetchrow("SELECT * FROM orders WHERE id = $1", order["id"])
    assert (row["refunded_satang"], row["status"]) == (order["total_satang"], "REFUNDED")


async def test_many_partial_refunds_never_exceed_the_amount_paid(client, db, send_webhook):
    order = await _completed_order(client, db, send_webhook)  # total 34,900
    results = await asyncio.gather(*(_try_refund(db, order["id"], 10000) for _ in range(20)))
    assert Counter(results)["ok"] == 3
    row = await db.fetchrow("SELECT * FROM orders WHERE id = $1", order["id"])
    assert (row["refunded_satang"], row["status"]) == (30000, "COMPLETED")
    assert await db.fetchval(
        "SELECT sum(amount_satang) FROM refunds WHERE order_id = $1", order["id"]
    ) == 30000
