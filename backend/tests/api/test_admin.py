import asyncio
import os
import uuid
from collections import Counter

import httpx
import pytest

from tests.api.conftest import order_body, post_order, wait_for_status

API = "/api/v1/admin"
PASSWORD = os.environ["SEED_ADMIN_PASSWORD"]
GAME = os.environ["MOCK_GAME_TEST_URL"]
ROLES = ("viewer", "support", "finance", "superadmin")


async def login(client: httpx.AsyncClient, role: str) -> dict[str, str]:
    response = await client.post(
        f"{API}/auth/login", json={"email": f"{role}@inblackm.test", "password": PASSWORD}
    )
    assert response.status_code == 200
    return {
        "Cookie": f"ib_admin={response.cookies['ib_admin']}",
        "X-CSRF-Token": response.json()["csrf_token"],
    }


async def completed_order(client, db, send_webhook, email: str | None = None) -> dict:
    order = (await post_order(client, order_body(email=email))).json()
    await send_webhook(order["id"], order["total_satang"])
    await wait_for_status(db, order["id"], "COMPLETED")
    return order


async def refund(client, headers, order_id: str, amount: int, key: str | None = None):
    return await client.post(
        f"{API}/orders/{order_id}/refund",
        json={"amount_satang": amount, "reason": "customer request"},
        headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex},
    )


async def test_wrong_password_and_unknown_user_look_the_same(client):
    wrong = await client.post(
        f"{API}/auth/login", json={"email": "viewer@inblackm.test", "password": "nope"}
    )
    unknown = await client.post(
        f"{API}/auth/login", json={"email": "ghost@inblackm.test", "password": "nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.content == unknown.content


async def test_anonymous_cannot_reach_admin(client):
    assert (await client.get(f"{API}/orders")).status_code == 401


async def test_customer_session_is_not_an_admin_session(client, db):
    response = await client.get(f"{API}/orders", headers={"Cookie": "ib_admin=forged"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "minimum"),
    [
        ("GET", "/orders", "viewer"),
        ("GET", "/dashboard", "viewer"),
        ("POST", "/orders/ORD-1000000000000-081/retry-fulfillment", "support"),
        ("GET", "/audit-logs", "finance"),
        ("GET", "/reconciliation", "finance"),
        ("GET", "/products", "superadmin"),
        ("GET", "/vouchers", "superadmin"),
        ("GET", "/admin-users", "superadmin"),
    ],
)
async def test_role_matrix(client, method, path, minimum):
    allowed_from = ROLES.index(minimum)
    for index, role in enumerate(ROLES):
        headers = await login(client, role)
        response = await client.request(method, f"{API}{path}", headers=headers)
        if index < allowed_from:
            assert response.status_code == 403, role
        else:
            assert response.status_code != 403, role


@pytest.mark.parametrize("role", ["viewer", "support"])
async def test_only_finance_can_refund(client, role):
    headers = await login(client, role)
    response = await refund(client, headers, "ORD-1000000000000-081", 100)
    assert response.status_code == 403


async def test_admin_write_needs_csrf(client):
    headers = await login(client, "finance")
    response = await refund(client, {"Cookie": headers["Cookie"]}, "ORD-1000000000000-081", 100)
    assert (response.status_code, response.json()["code"]) == (403, "CSRF_FAILED")


async def test_viewer_sees_masked_pii_and_support_sees_it_in_full(client):
    email = f"{uuid.uuid4().hex}@example.com"
    order = (await post_order(client, order_body(email=email))).json()
    viewer = await login(client, "viewer")
    support = await login(client, "support")

    masked = (await client.get(f"{API}/orders", headers=viewer)).json()["items"][0]
    assert masked["email"].startswith(email[0] + "***@")
    assert masked["game_uid"] == "12***89"
    detail = (await client.get(f"{API}/orders/{order['id']}", headers=viewer)).json()
    assert email not in str(detail)

    full = (await client.get(f"{API}/orders", headers=support)).json()["items"][0]
    assert (full["email"], full["game_uid"]) == (email, "123456789")


async def test_order_detail_has_timeline_webhooks_and_top_up_history(client, db, send_webhook):
    order = await completed_order(client, db, send_webhook)
    headers = await login(client, "support")
    detail = (await client.get(f"{API}/orders/{order['id']}", headers=headers)).json()
    assert [e["to_status"] for e in detail["timeline"]][-1] == "COMPLETED"
    assert detail["webhooks"][0]["outcome"] == "paid"
    assert detail["fulfillment_attempts"][0]["action"] == "POST"


async def test_cursor_pagination_has_no_gaps_or_repeats_while_orders_arrive(client):
    created = [(await post_order(client, order_body())).json()["id"] for _ in range(25)]
    headers = await login(client, "viewer")
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 7, **({"cursor": cursor} if cursor else {})}
        page = (await client.get(f"{API}/orders", params=params, headers=headers)).json()
        seen.extend(item["id"] for item in page["items"])
        await post_order(client, order_body())  # a new order arrives between pages
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen))
    assert set(created) <= set(seen)


async def test_filters(client, db):
    email = f"{uuid.uuid4().hex}@example.com"
    order = (await post_order(client, order_body(email=email))).json()
    await post_order(client, order_body())
    await db.execute("UPDATE orders SET status = 'EXPIRED' WHERE id = $1", order["id"])
    headers = await login(client, "support")

    async def ids(**params) -> list[str]:
        response = await client.get(f"{API}/orders", params=params, headers=headers)
        return [item["id"] for item in response.json()["items"]]

    assert await ids(status="EXPIRED") == [order["id"]]
    assert await ids(q=email) == [order["id"]]
    assert await ids(q=order["id"]) == [order["id"]]
    assert await ids(date_to="2000-01-01") == []


async def test_invalid_cursor_is_a_clean_error(client):
    headers = await login(client, "viewer")
    response = await client.get(f"{API}/orders", params={"cursor": "%%%"}, headers=headers)
    assert (response.status_code, response.json()["code"]) == (400, "INVALID_CURSOR")


# ---- refunds over HTTP


async def test_partial_then_full_refund(client, db, send_webhook):
    order = await completed_order(client, db, send_webhook)  # 34,900
    headers = await login(client, "finance")

    first = await refund(client, headers, order["id"], 4900)
    assert (first.status_code, first.json()["order_status"]) == (201, "COMPLETED")
    second = await refund(client, headers, order["id"], 30000)
    assert (second.status_code, second.json()["order_status"]) == (201, "REFUNDED")
    third = await refund(client, headers, order["id"], 1)
    assert third.status_code == 409


async def test_refund_above_the_balance_is_refused(client, db, send_webhook):
    order = await completed_order(client, db, send_webhook)
    headers = await login(client, "finance")
    response = await refund(client, headers, order["id"], order["total_satang"] + 1)
    assert (response.status_code, response.json()["code"]) == (409, "REFUND_EXCEEDS_PAID")


async def test_two_finance_users_refunding_at_once_over_http(client, db, send_webhook):
    order = await completed_order(client, db, send_webhook)
    first = await login(client, "finance")
    second = await login(client, "superadmin")
    responses = await asyncio.gather(
        *(
            refund(client, first if i % 2 else second, order["id"], order["total_satang"])
            for i in range(20)
        )
    )
    assert Counter(r.status_code for r in responses) == {201: 1, 409: 19}
    assert await db.fetchval(
        "SELECT sum(amount_satang) FROM refunds WHERE order_id = $1", order["id"]
    ) == order["total_satang"]


async def test_refund_idempotency_key_applies_once_and_replays(client, db, send_webhook):
    order = await completed_order(client, db, send_webhook)
    headers = await login(client, "finance")
    key = uuid.uuid4().hex
    responses = await asyncio.gather(
        *(refund(client, headers, order["id"], 1000, key=key) for _ in range(20))
    )
    assert {r.status_code for r in responses} == {201}
    assert len({r.content for r in responses}) == 1
    assert await db.fetchval(
        "SELECT refunded_satang FROM orders WHERE id = $1", order["id"]
    ) == 1000


async def test_refund_is_audited(client, db, send_webhook):
    order = await completed_order(client, db, send_webhook)
    headers = await login(client, "finance")
    await refund(client, headers, order["id"], 100)
    logs = (await client.get(f"{API}/audit-logs", headers=headers)).json()["items"]
    assert logs[0]["action"] == "order.refund"
    assert logs[0]["actor"] == "admin:finance@inblackm.test"
    assert logs[0]["target"] == order["id"]


# ---- retry


async def test_support_retry_after_failure_tops_up_exactly_once(client, db, send_webhook):
    async with httpx.AsyncClient(base_url=GAME, timeout=10) as game:
        await game.post("/_test/config", json={"FAIL_RATE": 1})
        order = (await post_order(client, order_body([("FLASH-SKIN-01", 1)]))).json()
        await send_webhook(order["id"], order["total_satang"])
        await wait_for_status(db, order["id"], "FAILED")
        await game.post("/_test/config", json={"FAIL_RATE": 0})

        headers = await login(client, "support")
        response = await client.post(
            f"{API}/orders/{order['id']}/retry-fulfillment", headers=headers
        )
        assert response.status_code == 200
        await wait_for_status(db, order["id"], "COMPLETED")
        stats = (await game.get("/stats")).json()
    assert stats[f"{order['id']}-1"] == 1
    assert await db.fetchval(
        "SELECT stock_remaining FROM products WHERE sku = 'FLASH-SKIN-01'"
    ) == 99


async def test_retry_is_refused_for_a_completed_order(client, db, send_webhook):
    order = await completed_order(client, db, send_webhook)
    headers = await login(client, "support")
    response = await client.post(f"{API}/orders/{order['id']}/retry-fulfillment", headers=headers)
    assert (response.status_code, response.json()["code"]) == (409, "INVALID_TRANSITION")


async def test_retry_is_refused_when_the_payment_was_wrong(client, db, send_webhook):
    order = (await post_order(client, order_body())).json()
    await send_webhook(order["id"], order["total_satang"] - 1)
    headers = await login(client, "support")
    response = await client.post(f"{API}/orders/{order['id']}/retry-fulfillment", headers=headers)
    assert (response.status_code, response.json()["code"]) == (409, "ORDER_NOT_RETRYABLE")


# ---- reconciliation


async def test_reconciliation_reports_each_kind_of_difference(client, db, send_webhook):
    matched = (await post_order(client, order_body())).json()
    await send_webhook(matched["id"], matched["total_satang"], event_id="evt_ok")
    wrong_amount = (await post_order(client, order_body())).json()
    await send_webhook(wrong_amount["id"], wrong_amount["total_satang"], event_id="evt_amount")
    only_ours = (await post_order(client, order_body())).json()
    await send_webhook(only_ours["id"], only_ours["total_satang"], event_id="evt_ours")

    t = "2026-11-06T10:15:00Z"
    csv = (
        "event_id,order_id,type,amount_satang,settled_at\n"
        f"evt_ok,{matched['id']},payment.succeeded,{matched['total_satang']},{t}\n"
        f"evt_amount,{wrong_amount['id']},payment.succeeded,1,{t}\n"
        f"evt_theirs,ORD-1000000000000-081,payment.succeeded,500,{t}\n"
    )
    headers = await login(client, "finance")
    response = await client.post(
        f"{API}/reconciliation",
        files={"file": ("settlement.csv", csv.encode(), "text/csv")},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["summary"] == {
        "amount_mismatch": 1,
        "missing_at_provider": 1,
        "missing_in_system": 1,
    }


async def test_reconciliation_against_the_provider_file_matches(client, db):
    async with httpx.AsyncClient(base_url=os.environ["MOCK_PAY_TEST_URL"], timeout=10) as pay:
        order = (await post_order(client, order_body())).json()
        await pay.post(
            f"/checkout/{order['id']}/success", params={"amount": order["total_satang"]}
        )
        await wait_for_status(db, order["id"], "COMPLETED")
        settlement = (await pay.get("/settlement.csv")).text

    headers = await login(client, "finance")
    lines = settlement.splitlines()
    ours = "\n".join([lines[0], *(line for line in lines[1:] if order["id"] in line)]) + "\n"
    response = await client.post(
        f"{API}/reconciliation",
        content=ours,
        headers={**headers, "Content-Type": "text/csv"},
    )
    assert response.json()["mismatch_count"] == 0


async def test_malformed_settlement_file_is_rejected(client):
    headers = await login(client, "finance")
    response = await client.post(
        f"{API}/reconciliation", content="a,b\n", headers={**headers, "Content-Type": "text/csv"}
    )
    assert (response.status_code, response.json()["code"]) == (422, "INVALID_CSV")


# ---- catalogue management and dashboard


async def test_price_change_does_not_alter_existing_orders(client, db):
    order = (await post_order(client, order_body([("DIAMOND-1000", 1)]))).json()
    headers = await login(client, "superadmin")
    response = await client.patch(
        f"{API}/products/DIAMOND-1000", json={"price_satang": 100}, headers=headers
    )
    assert response.status_code == 200
    assert await db.fetchval(
        "SELECT total_satang FROM orders WHERE id = $1", order["id"]
    ) == 34900
    new = (await post_order(client, order_body([("DIAMOND-1000", 1)]))).json()
    assert new["total_satang"] == 100


async def test_markup_in_a_product_name_is_stored_and_returned_as_plain_text(client):
    name = "<img src=x onerror=alert(1)>"
    headers = await login(client, "superadmin")
    await client.patch(f"{API}/products/DIAMOND-1000", json={"name": name}, headers=headers)
    product = (await client.get("/api/v1/products/diamond-1000")).json()
    assert product["name"] == name


async def test_float_price_is_rejected(client):
    headers = await login(client, "superadmin")
    response = await client.patch(
        f"{API}/products/DIAMOND-1000", json={"price_satang": 99.5}, headers=headers
    )
    assert response.status_code == 422


async def test_dashboard_figures(client, db, send_webhook):
    await completed_order(client, db, send_webhook)
    headers = await login(client, "viewer")
    figures = (await client.get(f"{API}/dashboard", headers=headers)).json()
    assert figures["orders_last_minute"] == 1
    assert figures["sales_today_satang"] == 34900
    assert {"queue_depth", "dead_letter"} <= set(figures)


async def test_superadmin_can_add_an_admin_who_can_then_log_in(client):
    headers = await login(client, "superadmin")
    response = await client.post(
        f"{API}/admin-users",
        json={
            "email": "new.viewer@inblackm.test",
            "password": "a-long-enough-pass",
            "role": "viewer",
        },
        headers=headers,
    )
    assert response.status_code == 201
    signed_in = await client.post(
        f"{API}/auth/login",
        json={"email": "new.viewer@inblackm.test", "password": "a-long-enough-pass"},
    )
    assert signed_in.json()["role"] == "viewer"
