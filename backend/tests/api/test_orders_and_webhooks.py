import json
import time
import uuid

import pytest

from app.domain.order_number import is_valid_order_number
from app.services import orders as order_service
from tests.api.conftest import (
    PAID_FLOW,
    order_body,
    post_order,
    signed,
    wait_for_status,
    webhook_bytes,
)

FLASH = "FLASH-SKIN-01"
HOOK = "/api/v1/webhooks/payment"


async def _expire(db, order_id: str) -> None:
    await db.execute("UPDATE orders SET expires_at = now() - interval '1 second'")
    await order_service.expire_due_orders(db)  # the worker may also get there first
    await wait_for_status(db, order_id, "EXPIRED")


@pytest.mark.parametrize(
    ("items", "voucher", "method", "expected"),
    [
        ([("DIAMOND-1000", 2)], "SAVE10", "card", (69800, 5000, 64800, 2180, 66980, 4382)),
        (
            [("MONTHLY-PASS", 1), (FLASH, 1)],
            "FLAT20",
            "truemoney",
            (27800, 2000, 25800, 387, 26187, 1713),
        ),
        ([("DIAMOND-5000", 1)], "SAVE10", "promptpay", (159000, 5000, 154000, 0, 154000, 10075)),
    ],
)
async def test_spec_prices_end_to_end(client, items, voucher, method, expected):
    response = await post_order(client, order_body(items, voucher=voucher, method=method))
    assert response.status_code == 201
    body = response.json()
    assert is_valid_order_number(body["id"])
    assert body["status"] == "PENDING_PAYMENT"
    assert tuple(
        body[f"{name}_satang"] for name in ("subtotal", "discount", "net", "fee", "total", "vat")
    ) == expected


async def test_quote_matches_the_order_and_changes_nothing(client, db):
    payload = {
        "items": [{"sku": "DIAMOND-1000", "qty": 2}],
        "payment_method": "card",
        "voucher_code": "SAVE10",
    }
    quote = (await client.post("/api/v1/orders/quote", json=payload)).json()
    assert quote["total_satang"] == 66980
    assert await db.fetchval("SELECT used_count FROM vouchers WHERE code = 'SAVE10'") == 0


async def test_missing_idempotency_key_is_rejected(client):
    response = await client.post("/api/v1/orders", json=order_body())
    assert (response.status_code, response.json()["code"]) == (400, "IDEMPOTENCY_KEY_REQUIRED")


async def test_idempotency_key_with_a_different_body_is_rejected(client):
    key = uuid.uuid4().hex
    headers = {"Idempotency-Key": key}
    assert (await client.post("/api/v1/orders", json=order_body(), headers=headers)).is_success
    response = await client.post("/api/v1/orders", json=order_body(), headers=headers)
    assert (response.status_code, response.json()["code"]) == (422, "IDEMPOTENCY_KEY_REUSED")


async def test_a_refusal_is_replayed_too(client):
    key = uuid.uuid4().hex
    body = order_body([("DIAMOND-1000", 1)], voucher="NOPE")
    headers = {"Idempotency-Key": key}
    first = await client.post("/api/v1/orders", json=body, headers=headers)
    second = await client.post("/api/v1/orders", json=body, headers=headers)
    assert first.status_code == second.status_code == 422
    assert first.content == second.content


async def test_monthly_pass_is_one_per_order(client):
    response = await post_order(client, order_body([("MONTHLY-PASS", 2)]))
    assert (response.status_code, response.json()["code"]) == (422, "PER_ORDER_LIMIT")


async def test_voucher_minimum_spend(client):
    response = await post_order(client, order_body([("MONTHLY-PASS", 1)], voucher="SAVE10"))
    assert (response.status_code, response.json()["code"]) == (422, "VOUCHER_MIN_SPEND")


async def test_float_quantity_is_rejected(client):
    body = order_body()
    body["items"][0]["qty"] = 1.5
    response = await post_order(client, body)
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR")


async def test_price_is_locked_after_order_creation(client, db, send_webhook):
    created = (await post_order(client, order_body([("DIAMOND-1000", 1)]))).json()
    await db.execute("UPDATE products SET price_satang = 1 WHERE sku = 'DIAMOND-1000'")
    hook = await send_webhook(created["id"], 34900)
    assert hook.json()["outcome"] == "paid"
    assert await db.fetchval(
        "SELECT unit_price_satang FROM order_items WHERE order_id = $1", created["id"]
    ) == 34900


# ---- webhook authentication


async def test_signature_is_checked_against_raw_bytes(client):
    created = (await post_order(client, order_body())).json()
    body = json.loads(webhook_bytes(created["id"], created["total_satang"]))
    raw = json.dumps(body, indent=4, sort_keys=True).encode()  # unusual but legal formatting
    response = await client.post(HOOK, content=raw, headers=signed(raw))
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-IBM-Signature": "garbage"},
        {"X-IBM-Signature": "t=abc,v1=00"},
        {"X-IBM-Signature": "t=1,v1=00"},
    ],
)
async def test_bad_signatures_are_rejected(client, headers):
    raw = webhook_bytes("ORD-1000000000000-030", 100)
    response = await client.post(HOOK, content=raw, headers=headers)
    assert (response.status_code, response.json()["code"]) == (401, "INVALID_SIGNATURE")


async def test_wrong_secret_is_rejected(client):
    raw = webhook_bytes("ORD-1000000000000-030", 100)
    response = await client.post(HOOK, content=raw, headers=signed(raw, secret="whsec_wrong"))
    assert response.status_code == 401


async def test_tampered_body_is_rejected(client):
    raw = webhook_bytes("ORD-1000000000000-030", 100)
    response = await client.post(
        HOOK, content=raw.replace(b"100", b"999"), headers=signed(raw)
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("skew", "accepted"), [(-290, True), (290, True), (-310, False), (310, False)]
)
async def test_timestamp_tolerance(client, skew, accepted):
    raw = webhook_bytes("ORD-1000000000000-030", 100)
    response = await client.post(HOOK, content=raw, headers=signed(raw, int(time.time()) + skew))
    assert response.status_code == (200 if accepted else 401)


async def test_unknown_order_is_acknowledged_and_kept_for_reconciliation(client, db, send_webhook):
    response = await send_webhook("ORD-1000000000000-030", 100, event_id="evt_unknown")
    assert (response.status_code, response.json()["outcome"]) == (200, "unknown_order")
    assert await db.fetchval(
        "SELECT outcome FROM webhook_events WHERE event_id = 'evt_unknown'"
    ) == "unknown_order"


# ---- webhook ordering and edge cases


async def test_amount_mismatch_goes_to_review_and_is_not_fulfilled(client, db, send_webhook):
    created = (await post_order(client, order_body())).json()
    hook = await send_webhook(created["id"], created["total_satang"] - 1)
    assert hook.json()["outcome"] == "amount_mismatch"
    row = await db.fetchrow("SELECT * FROM orders WHERE id = $1", created["id"])
    assert (row["status"], row["paid_satang"]) == ("NEEDS_REVIEW", created["total_satang"] - 1)
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE kind = 'fulfill_order'") == 0


async def test_failed_after_succeeded_does_not_move_the_order_back(client, db, send_webhook):
    created = (await post_order(client, order_body())).json()
    await send_webhook(created["id"], created["total_satang"])
    hook = await send_webhook(created["id"], created["total_satang"], kind="payment.failed")
    assert hook.json()["outcome"] == "ignored_out_of_order"
    assert await db.fetchval("SELECT status FROM orders WHERE id = $1", created["id"]) in PAID_FLOW


async def test_payment_failed_returns_stock_and_voucher(client, db, send_webhook):
    email = "failed@example.com"
    body = order_body([(FLASH, 2)], email=email, voucher="FLAT20")
    created = (await post_order(client, body)).json()
    await send_webhook(created["id"], created["total_satang"], kind="payment.failed")
    row = await db.fetchrow(
        "SELECT p.stock_remaining, v.used_count, o.status FROM products p, vouchers v, orders o "
        "WHERE p.sku = $1 AND v.code = 'FLAT20' AND o.id = $2",
        FLASH,
        created["id"],
    )
    assert tuple(row) == (100, 0, "PAYMENT_FAILED")


async def test_second_capture_under_a_new_event_id_is_flagged(client, db, send_webhook):
    created = (await post_order(client, order_body())).json()
    await send_webhook(created["id"], created["total_satang"])
    hook = await send_webhook(created["id"], created["total_satang"])
    assert hook.json()["outcome"] == "duplicate_capture"
    row = await db.fetchrow("SELECT * FROM orders WHERE id = $1", created["id"])
    assert (row["status"], row["paid_satang"]) == ("NEEDS_REVIEW", created["total_satang"] * 2)


# ---- expiry


async def test_expiry_returns_stock_voucher_and_email_quota(client, db):
    email = "expiry@example.com"
    body = order_body([(FLASH, 2)], email=email, voucher="FLAT20")
    created = (await post_order(client, body)).json()
    assert await db.fetchval("SELECT stock_remaining FROM products WHERE sku = $1", FLASH) == 98

    await _expire(db, created["id"])

    assert await db.fetchval("SELECT stock_remaining FROM products WHERE sku = $1", FLASH) == 100
    assert await db.fetchval("SELECT used_count FROM vouchers WHERE code = 'FLAT20'") == 0
    assert (await post_order(client, order_body([(FLASH, 2)], email=email))).status_code == 201


async def test_expiry_runs_once_only(client, db):
    created = (await post_order(client, order_body([(FLASH, 1)]))).json()
    await _expire(db, created["id"])
    assert await order_service.expire_due_orders(db) == 0
    assert await db.fetchval("SELECT stock_remaining FROM products WHERE sku = $1", FLASH) == 100


async def test_order_within_its_window_is_not_expired(client, db):
    created = (await post_order(client, order_body())).json()
    async with db.acquire() as conn, conn.transaction():
        assert not await order_service.expire_order(conn, created["id"])


async def test_late_payment_with_stock_available_is_fulfilled(client, db, send_webhook):
    created = (await post_order(client, order_body([(FLASH, 2)], voucher="FLAT20"))).json()
    await _expire(db, created["id"])

    hook = await send_webhook(created["id"], created["total_satang"])

    assert hook.json()["outcome"] == "late_paid"
    row = await db.fetchrow(
        "SELECT o.status, p.stock_remaining, v.used_count FROM orders o, products p, vouchers v "
        "WHERE o.id = $1 AND p.sku = $2 AND v.code = 'FLAT20'",
        created["id"],
        FLASH,
    )
    assert row["status"] in PAID_FLOW
    assert (row["stock_remaining"], row["used_count"]) == (98, 1)


async def test_late_payment_without_stock_is_refunded_automatically(client, db, send_webhook):
    created = (await post_order(client, order_body([(FLASH, 1)]))).json()
    await _expire(db, created["id"])
    await db.execute("UPDATE products SET stock_remaining = 0 WHERE sku = $1", FLASH)

    hook = await send_webhook(created["id"], created["total_satang"])

    assert hook.json()["outcome"] == "late_refunded"
    row = await db.fetchrow("SELECT * FROM orders WHERE id = $1", created["id"])
    assert (row["status"], row["paid_satang"], row["refunded_satang"]) == (
        "REFUNDED",
        created["total_satang"],
        created["total_satang"],
    )
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE kind = 'refund_provider'") == 1
    assert await db.fetchval("SELECT stock_remaining FROM products WHERE sku = $1", FLASH) == 0


async def test_late_payment_with_voucher_exhausted_is_refunded(client, db, send_webhook):
    body = order_body([("DIAMOND-1000", 1)], voucher="SAVE10")
    created = (await post_order(client, body)).json()
    await _expire(db, created["id"])
    await db.execute("UPDATE vouchers SET used_count = max_total_uses WHERE code = 'SAVE10'")

    hook = await send_webhook(created["id"], created["total_satang"])

    assert hook.json()["outcome"] == "late_refunded"
    assert await db.fetchval("SELECT used_count FROM vouchers WHERE code = 'SAVE10'") == 10


async def test_late_payment_with_wrong_amount_goes_to_review(client, db, send_webhook):
    created = (await post_order(client, order_body())).json()
    await _expire(db, created["id"])
    hook = await send_webhook(created["id"], created["total_satang"] + 100)
    assert hook.json()["outcome"] == "amount_mismatch"
    assert await db.fetchval(
        "SELECT status FROM orders WHERE id = $1", created["id"]
    ) == "NEEDS_REVIEW"


# ---- provider refund events


async def test_external_refund_from_the_provider_is_recorded(client, db, send_webhook):
    created = (await post_order(client, order_body())).json()
    await send_webhook(created["id"], created["total_satang"])
    await wait_for_status(db, created["id"], "COMPLETED")

    hook = await send_webhook(created["id"], 1000, kind="payment.refunded")

    assert hook.json()["outcome"] == "refund_external"
    row = await db.fetchrow("SELECT * FROM orders WHERE id = $1", created["id"])
    assert (row["status"], row["refunded_satang"]) == ("COMPLETED", 1000)
    assert await db.fetchval(
        "SELECT count(*) FROM ledger_entries WHERE kind = 'refund' AND order_id = $1",
        created["id"],
    ) == 1


async def test_refund_webhook_above_the_balance_goes_to_review(client, db, send_webhook):
    created = (await post_order(client, order_body())).json()
    await send_webhook(created["id"], created["total_satang"])
    hook = await send_webhook(created["id"], created["total_satang"] + 1, kind="payment.refunded")
    assert hook.json()["outcome"] == "refund_exceeds_paid"
    row = await db.fetchrow("SELECT * FROM orders WHERE id = $1", created["id"])
    assert (row["status"], row["refunded_satang"]) == ("NEEDS_REVIEW", 0)
