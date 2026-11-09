"""Worker, mock-game and mock-pay working together for real."""

import asyncio
import os
from collections.abc import AsyncIterator

import httpx
import pytest

from app.services import refunds
from tests.api.conftest import order_body, post_order, wait_for_status

GAME = os.environ["MOCK_GAME_TEST_URL"]
PAY = os.environ["MOCK_PAY_TEST_URL"]
FLASH = "FLASH-SKIN-01"
NORMAL = {"FAIL_RATE": 0, "TIMEOUT_RATE": 0, "SUCCESS_BUT_TIMEOUT_RATE": 0, "LATENCY_MS": 0}


@pytest.fixture
async def game() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=GAME, timeout=30) as http:
        assert (await http.post("/_test/config", json=NORMAL)).status_code == 200
        yield http
        await http.post("/_test/config", json=NORMAL)


@pytest.fixture
async def pay() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=PAY, timeout=30) as http:
        yield http


async def _success_counts(game: httpx.AsyncClient, order_id: str) -> dict[str, int]:
    stats = (await game.get("/stats")).json()
    return {ref: count for ref, count in stats.items() if ref.startswith(order_id)}


async def _buy_and_pay(client, send_webhook, items=None) -> dict:
    created = (await post_order(client, order_body(items))).json()
    await send_webhook(created["id"], created["total_satang"])
    return created


async def _click(pay, order: dict, action: str) -> None:
    response = await pay.post(
        f"/checkout/{order['id']}/{action}", params={"amount": order["total_satang"]}
    )
    assert response.status_code == 200


async def test_paid_order_is_delivered_exactly_once(client, db, game, send_webhook):
    order = await _buy_and_pay(client, send_webhook)
    await wait_for_status(db, order["id"], "COMPLETED")
    assert await _success_counts(game, order["id"]) == {f"{order['id']}-1": 1}
    rows = await db.fetch(
        "SELECT to_status FROM order_events WHERE order_id = $1 ORDER BY seq", order["id"]
    )
    assert [r["to_status"] for r in rows] == [
        "PENDING_PAYMENT",
        "PAID",
        "IN_QUEUE",
        "IN_PROGRESS",
        "COMPLETED",
    ]


async def test_every_order_line_gets_its_own_single_top_up(client, db, game, send_webhook):
    order = await _buy_and_pay(
        client, send_webhook, [("DIAMOND-1000", 2), ("DIAMOND-5000", 1), (FLASH, 1)]
    )
    await wait_for_status(db, order["id"], "COMPLETED")
    counts = await _success_counts(game, order["id"])
    assert sorted(counts.values()) == [1, 1, 1]


async def test_success_but_timeout_does_not_top_up_twice(client, db, game, send_webhook):
    await game.post("/_test/config", json={"SUCCESS_BUT_TIMEOUT_RATE": 1})
    order = await _buy_and_pay(client, send_webhook)
    await wait_for_status(db, order["id"], "COMPLETED")
    assert await _success_counts(game, order["id"]) == {f"{order['id']}-1": 1}
    actions = await db.fetch(
        "SELECT action, outcome FROM fulfillment_attempts WHERE order_id = $1 ORDER BY id",
        order["id"],
    )
    assert [(a["action"], a["outcome"]) for a in actions] == [
        ("POST", "UNREACHABLE"),
        ("GET", "APPLIED"),
    ]


async def test_permanent_failure_ends_in_failed_and_dead_letter(client, db, game, send_webhook):
    await game.post("/_test/config", json={"FAIL_RATE": 1})
    order = await _buy_and_pay(client, send_webhook, [(FLASH, 1)])
    await wait_for_status(db, order["id"], "FAILED")
    assert await _success_counts(game, order["id"]) == {}
    assert await db.fetchval(
        "SELECT status FROM jobs WHERE dedupe_key = $1", f"fulfill:{order['id']}"
    ) == "dead"
    assert await db.fetchval("SELECT stock_remaining FROM products WHERE sku = $1", FLASH) == 100


async def test_timeouts_that_did_not_apply_end_in_failed(client, db, game, send_webhook):
    await game.post("/_test/config", json={"TIMEOUT_RATE": 1})
    order = await _buy_and_pay(client, send_webhook)
    await wait_for_status(db, order["id"], "FAILED")
    assert await _success_counts(game, order["id"]) == {}


async def test_flaky_game_service_never_causes_a_double_top_up(client, db, game, send_webhook):
    await game.post(
        "/_test/config",
        json={"FAIL_RATE": 0.3, "TIMEOUT_RATE": 0.1, "SUCCESS_BUT_TIMEOUT_RATE": 0.3},
    )
    created = await asyncio.gather(*(_buy_and_pay(client, send_webhook) for _ in range(30)))
    terminal = {"COMPLETED", "FAILED", "NEEDS_REVIEW"}
    for _ in range(1500):
        rows = await db.fetch("SELECT status FROM orders")
        if {r["status"] for r in rows} <= terminal:
            break
        await asyncio.sleep(0.1)
    assert {r["status"] for r in await db.fetch("SELECT status FROM orders")} <= terminal
    for order in created:
        counts = await _success_counts(game, order["id"])
        assert all(count == 1 for count in counts.values()), counts
        status = await db.fetchval("SELECT status FROM orders WHERE id = $1", order["id"])
        if status == "COMPLETED":
            assert len(counts) == 1
        elif status == "FAILED":
            assert counts == {}


async def test_checkout_page_lists_every_scenario(client, pay):
    order = (await post_order(client, order_body())).json()
    url = order["checkout_url"].replace("http://localhost:8090", "")
    page = await pay.get(url)
    assert page.status_code == 200
    assert page.text.count("<button>") == 5


async def test_pay_button_success(client, db, game, pay):
    order = (await post_order(client, order_body())).json()
    await _click(pay, order, "success")
    await wait_for_status(db, order["id"], "COMPLETED")


async def test_pay_button_failure(client, db, game, pay):
    order = (await post_order(client, order_body())).json()
    await _click(pay, order, "fail")
    await wait_for_status(db, order["id"], "PAYMENT_FAILED")


async def test_pay_button_duplicate_webhooks_apply_once(client, db, game, pay):
    order = (await post_order(client, order_body())).json()
    await _click(pay, order, "duplicate")
    await wait_for_status(db, order["id"], "COMPLETED")
    await asyncio.sleep(1)
    assert await db.fetchval(
        "SELECT count(*) FROM ledger_entries WHERE order_id = $1", order["id"]
    ) == 1
    assert await _success_counts(game, order["id"]) == {f"{order['id']}-1": 1}


async def test_pay_button_wrong_amount(client, db, game, pay):
    order = (await post_order(client, order_body())).json()
    await _click(pay, order, "wrong_amount")
    await wait_for_status(db, order["id"], "NEEDS_REVIEW")
    assert await _success_counts(game, order["id"]) == {}


async def test_double_clicking_pay_charges_once(client, db, game, pay):
    order = (await post_order(client, order_body())).json()
    await _click(pay, order, "success")
    await _click(pay, order, "success")
    await wait_for_status(db, order["id"], "COMPLETED")
    await asyncio.sleep(1)
    assert await db.fetchval(
        "SELECT paid_satang FROM orders WHERE id = $1", order["id"]
    ) == order["total_satang"]


async def test_worker_expires_orders_on_its_own(client, db):
    order = (await post_order(client, order_body([(FLASH, 1)]))).json()
    await db.execute("UPDATE orders SET expires_at = now() - interval '1 second'")
    await wait_for_status(db, order["id"], "EXPIRED", within_seconds=15)
    assert await db.fetchval("SELECT stock_remaining FROM products WHERE sku = $1", FLASH) == 100


async def test_refund_goes_to_the_provider_and_is_confirmed_by_webhook(
    client, db, game, send_webhook
):
    order = await _buy_and_pay(client, send_webhook)
    await wait_for_status(db, order["id"], "COMPLETED")
    async with db.acquire() as conn, conn.transaction():
        await refunds.request_refund(conn, order["id"], 1000, "partial", "finance@test")

    for _ in range(100):
        if await db.fetchval("SELECT status FROM refunds") == "SUCCEEDED":
            break
        await asyncio.sleep(0.1)

    assert await db.fetchval("SELECT status FROM refunds") == "SUCCEEDED"
    assert await db.fetchval(
        "SELECT amount_satang FROM ledger_entries WHERE kind = 'refund' AND order_id = $1",
        order["id"],
    ) == 1000
    assert await db.fetchval(
        "SELECT refunded_satang FROM orders WHERE id = $1", order["id"]
    ) == 1000
