import asyncio
import os
import re
import time
import uuid

import httpx
import pytest

from tests.api.conftest import order_body, post_order

MAILPIT = os.environ["MAILPIT_URL"]
API = "/api/v1"


async def latest_otp(email: str) -> str:
    async with httpx.AsyncClient(base_url=MAILPIT, timeout=10) as mail:
        for _ in range(50):
            found = (await mail.get("/api/v1/search", params={"query": f'to:"{email}"'})).json()
            if found["messages"]:
                return re.search(r"[0-9]{6}", found["messages"][0]["Subject"]).group()
            await asyncio.sleep(0.1)
    raise AssertionError("no OTP email arrived")


async def sign_in(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    assert (await client.post(f"{API}/auth/otp/request", json={"email": email})).status_code == 202
    code = await latest_otp(email)
    response = await client.post(f"{API}/auth/otp/verify", json={"email": email, "code": code})
    assert response.status_code == 200
    return {
        "Cookie": f"ib_session={response.cookies['ib_session']}",
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def unique_email() -> str:
    return f"{uuid.uuid4().hex}@example.com"


async def test_otp_login_sets_a_hardened_cookie(client):
    email = unique_email()
    await client.post(f"{API}/auth/otp/request", json={"email": email})
    code = await latest_otp(email)
    response = await client.post(f"{API}/auth/otp/verify", json={"email": email, "code": code})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


async def test_otp_cannot_be_reused(client):
    email = unique_email()
    await client.post(f"{API}/auth/otp/request", json={"email": email})
    code = await latest_otp(email)
    body = {"email": email, "code": code}
    assert (await client.post(f"{API}/auth/otp/verify", json=body)).status_code == 200
    second = await client.post(f"{API}/auth/otp/verify", json=body)
    assert (second.status_code, second.json()["code"]) == (400, "OTP_INVALID")


async def test_expired_otp_is_rejected(client, db):
    email = unique_email()
    await client.post(f"{API}/auth/otp/request", json={"email": email})
    code = await latest_otp(email)
    await db.execute("UPDATE otp_codes SET expires_at = now() - interval '1 second'")
    response = await client.post(f"{API}/auth/otp/verify", json={"email": email, "code": code})
    assert response.status_code == 400


async def _wrong_code(client, email: str, code: str):
    return await client.post(f"{API}/auth/otp/verify", json={"email": email, "code": code})


async def test_five_wrong_codes_lock_for_15_minutes_even_with_the_right_code(client, db):
    email = unique_email()
    await client.post(f"{API}/auth/otp/request", json={"email": email})
    code = await latest_otp(email)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert (await _wrong_code(client, email, wrong)).status_code == 400

    locked = await _wrong_code(client, email, code)

    assert (locked.status_code, locked.json()["code"]) == (429, "OTP_LOCKED")
    assert 880 <= int(locked.headers["retry-after"]) <= 900


async def test_requesting_a_new_code_does_not_clear_the_failure_count(client, db):
    email = unique_email()
    await client.post(f"{API}/auth/otp/request", json={"email": email})
    code = await latest_otp(email)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(3):
        await _wrong_code(client, email, wrong)
    await client.post(f"{API}/auth/otp/request", json={"email": email})
    assert await db.fetchval(
        "SELECT failed_attempts FROM otp_codes WHERE email = $1", email
    ) == 3


async def test_otp_is_not_sent_while_locked(client, db):
    email = unique_email()
    await client.post(f"{API}/auth/otp/request", json={"email": email})
    before = await db.fetchval("SELECT code_hash FROM otp_codes WHERE email = $1", email)
    await db.execute(
        "UPDATE otp_codes SET locked_until = now() + interval '10 minutes' WHERE email = $1", email
    )
    response = await client.post(f"{API}/auth/otp/request", json={"email": email})
    assert response.status_code == 202
    assert await db.fetchval(
        "SELECT code_hash FROM otp_codes WHERE email = $1", email
    ) == before


async def test_session_cookie_is_stored_only_as_a_hash(client, db):
    email = unique_email()
    headers = await sign_in(client, email)
    token = headers["Cookie"].split("=", 1)[1]
    assert await db.fetchval("SELECT count(*) FROM sessions WHERE token_hash = $1", token) == 0


# ---- ownership


async def test_owner_can_read_their_order(client):
    email = unique_email()
    order = (await post_order(client, order_body(email=email))).json()
    headers = await sign_in(client, email)
    response = await client.get(f"{API}/orders/{order['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["timeline"][0]["status"] == "PENDING_PAYMENT"


async def test_another_customer_gets_404_exactly_like_a_missing_order(client):
    order = (await post_order(client, order_body())).json()
    headers = await sign_in(client, unique_email())
    theirs = await client.get(f"{API}/orders/{order['id']}", headers=headers)
    missing = await client.get(f"{API}/orders/ORD-1000000000000-081", headers=headers)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.content == missing.content


async def test_anonymous_gets_404(client):
    order = (await post_order(client, order_body())).json()
    assert (await client.get(f"{API}/orders/{order['id']}")).status_code == 404


async def test_bad_checksum_is_refused(client):
    response = await client.get(f"{API}/orders/ORD-2587965432159-458")
    assert (response.status_code, response.json()["code"]) == (400, "INVALID_ORDER_ID")


async def test_my_orders_tabs(client, db):
    email = unique_email()
    current = (await post_order(client, order_body(email=email))).json()
    past = (await post_order(client, order_body(email=email))).json()
    await post_order(client, order_body())  # someone else's
    await db.execute(
        "UPDATE orders SET status = 'EXPIRED' WHERE id = $1", past["id"]
    )
    headers = await sign_in(client, email)
    now = (await client.get(f"{API}/me/orders", headers=headers)).json()["items"]
    done = (await client.get(f"{API}/me/orders?tab=past", headers=headers)).json()["items"]
    assert [o["id"] for o in now] == [current["id"]]
    assert [o["id"] for o in done] == [past["id"]]


async def test_my_orders_needs_sign_in(client):
    assert (await client.get(f"{API}/me/orders")).status_code == 401


async def test_state_changing_request_needs_a_csrf_token(client):
    headers = await sign_in(client, unique_email())
    without = await client.post(f"{API}/auth/logout", headers={"Cookie": headers["Cookie"]})
    assert (without.status_code, without.json()["code"]) == (403, "CSRF_FAILED")
    assert (await client.post(f"{API}/auth/logout", headers=headers)).status_code == 204
    assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 401


# ---- guest tracking


async def _track(client, order_id: str, email: str) -> httpx.Response:
    return await client.post(f"{API}/orders/track", json={"order_id": order_id, "email": email})


async def test_track_with_the_right_email(client):
    email = unique_email()
    order = (await post_order(client, order_body(email=email))).json()
    response = await _track(client, order["id"], email.upper())
    assert response.status_code == 200
    body = response.json()
    assert body["game_uid"] == "12***89"
    assert body["tracking_token"]


async def test_track_gives_the_same_answer_for_wrong_email_and_no_such_order(client):
    order = (await post_order(client, order_body())).json()
    wrong_email = await _track(client, order["id"], "nobody@example.com")
    no_order = await _track(client, "ORD-1000000000000-081", "nobody@example.com")
    assert wrong_email.status_code == no_order.status_code == 404
    assert wrong_email.content == no_order.content


async def test_track_response_time_does_not_reveal_whether_the_order_exists(client):
    email = unique_email()
    order = (await post_order(client, order_body(email=email))).json()

    async def timed(order_id: str, address: str) -> float:
        started = time.monotonic()
        await _track(client, order_id, address)
        return time.monotonic() - started

    found = await timed(order["id"], email)
    wrong = await timed(order["id"], "no@example.com")
    missing = await timed("ORD-1000000000000-081", "no@example.com")
    assert abs(wrong - missing) < 0.1
    assert abs(found - wrong) < 0.1


async def test_track_limit_per_order_is_5_per_minute(client):
    order = (await post_order(client, order_body())).json()
    codes = [(await _track(client, order["id"], "x@example.com")) for _ in range(6)]
    assert [r.status_code for r in codes] == [404] * 5 + [429]
    assert (codes[-1].json()["code"], "retry-after" in codes[-1].headers) == ("RATE_LIMITED", True)


async def test_track_limit_per_ip_is_10_per_minute(client):
    orders = [(await post_order(client, order_body())).json() for _ in range(11)]
    codes = [(await _track(client, o["id"], "x@example.com")).status_code for o in orders]
    assert codes == [404] * 10 + [429]


@pytest.mark.parametrize("path", ["/healthz", f"{API}/products"])
async def test_security_headers_are_present(client, path):
    headers = (await client.get(path)).headers
    assert "unsafe-inline" not in headers["content-security-policy"]
    assert "max-age" in headers["strict-transport-security"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"]


async def test_errors_share_one_format(client):
    response = await client.get(f"{API}/products/nope")
    assert response.headers["content-type"].startswith("application/problem+json")
    assert {"type", "title", "status", "code", "detail"} <= set(response.json())
    unknown_route = await client.get(f"{API}/does-not-exist")
    assert unknown_route.json()["code"] == "NOT_FOUND"
