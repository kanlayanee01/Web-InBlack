"""End-to-end API tests through the real proxy, both replicas, PostgreSQL and Redis.

These reset the database, so they only run against a stack started with APP_ENV=test.
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import asyncpg
import httpx
import pytest

SECRET = os.environ["PAYMENT_WEBHOOK_SECRET"]
EPIC_LIMITS = httpx.Limits(max_connections=None, max_keepalive_connections=100)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=os.environ["API_BASE_URL"], timeout=180, limits=EPIC_LIMITS
    ) as http:
        reset = await http.post("/api/v1/_test/reset")
        assert reset.status_code == 200, "start the stack with APP_ENV=test"
        yield http


@pytest.fixture
async def db() -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=20)
    yield pool
    await pool.close()


def order_body(
    items: list[tuple[str, int]] | None = None,
    email: str | None = None,
    voucher: str | None = None,
    method: str = "promptpay",
) -> dict[str, object]:
    return {
        "items": [{"sku": sku, "qty": qty} for sku, qty in (items or [("DIAMOND-1000", 1)])],
        "email": email or f"{uuid.uuid4().hex}@example.com",
        "game_uid": "123456789",
        "payment_method": method,
        "voucher_code": voucher,
    }


async def post_order(http: httpx.AsyncClient, body: dict[str, object]) -> httpx.Response:
    return await http.post(
        "/api/v1/orders", json=body, headers={"Idempotency-Key": uuid.uuid4().hex}
    )


def signed(raw: bytes, timestamp: int | None = None, secret: str = SECRET) -> dict[str, str]:
    stamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
    return {"X-IBM-Signature": f"t={stamp},v1={digest}", "Content-Type": "application/json"}


def webhook_bytes(
    order_id: str,
    amount: int,
    kind: str = "payment.succeeded",
    event_id: str | None = None,
    currency: str = "THB",
) -> bytes:
    return json.dumps(
        {
            "event_id": event_id or f"evt_{uuid.uuid4().hex}",
            "type": kind,
            "order_id": order_id,
            "amount_satang": amount,
            "currency": currency,
            "occurred_at": "2026-11-06T10:15:00Z",
        }
    ).encode()


Send = Callable[..., Awaitable[httpx.Response]]


@pytest.fixture
def send_webhook(client: httpx.AsyncClient) -> Send:
    async def send(order_id: str, amount: int, **kwargs: object) -> httpx.Response:
        raw = webhook_bytes(order_id, amount, **kwargs)  # type: ignore[arg-type]
        return await client.post("/api/v1/webhooks/payment", content=raw, headers=signed(raw))

    return send
