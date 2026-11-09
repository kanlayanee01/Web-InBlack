"""SSE across replicas: the stream is on one API instance, the webhook lands on the other."""

import asyncio
import json
import os
import time

import httpx

from tests.api.conftest import order_body, post_order, signed, wait_for_status, webhook_bytes

API_1 = os.environ["API_1_URL"]
API_2 = os.environ["API_2_URL"]


class Stream:
    """Reads an SSE stream in the background and records when each event arrived."""

    def __init__(self, base: str, order: dict, last_event_id: int | None = None) -> None:
        self.url = f"{base}/api/v1/orders/{order['id']}/events"
        self.params = {"token": order["tracking_token"]}
        self.headers = {} if last_event_id is None else {"Last-Event-ID": str(last_event_id)}
        self.events: list[tuple[int, str, float]] = []
        self.status_code: int | None = None
        self._task: asyncio.Task | None = None

    async def _read(self) -> None:
        async with (
            httpx.AsyncClient(timeout=None) as http,
            http.stream("GET", self.url, params=self.params, headers=self.headers) as response,
        ):
            self.status_code = response.status_code
            current_id = 0
            async for line in response.aiter_lines():
                if line.startswith("id: "):
                    current_id = int(line[4:])
                elif line.startswith("data: "):
                    status = json.loads(line[6:])["status"]
                    self.events.append((current_id, status, time.monotonic()))

    async def __aenter__(self) -> "Stream":
        self._task = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self._task is not None
        self._task.cancel()

    async def wait_for(self, status: str, within: int = 20) -> float:
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            for _, seen, at in self.events:
                if seen == status:
                    return at
            await asyncio.sleep(0.01)
        raise AssertionError(f"never saw {status}; got {self.events}")


async def _deliver(base: str, order: dict) -> float:
    raw = webhook_bytes(order["id"], order["total_satang"])
    async with httpx.AsyncClient(base_url=base, timeout=20) as http:
        sent_at = time.monotonic()
        response = await http.post("/api/v1/webhooks/payment", content=raw, headers=signed(raw))
    assert response.status_code == 200
    return sent_at


async def test_update_reaches_a_stream_on_the_other_replica_within_2_seconds(client):
    order = (await post_order(client, order_body())).json()
    async with Stream(API_1, order) as stream:
        await stream.wait_for("PENDING_PAYMENT")
        sent_at = await _deliver(API_2, order)
        arrived_at = await stream.wait_for("PAID")
    assert arrived_at - sent_at < 2


async def test_worker_updates_reach_the_stream_in_order(client):
    order = (await post_order(client, order_body())).json()
    async with Stream(API_1, order) as stream:
        await stream.wait_for("PENDING_PAYMENT")
        await _deliver(API_2, order)
        await stream.wait_for("COMPLETED")
    assert [(seq, status) for seq, status, _ in stream.events] == [
        (1, "PENDING_PAYMENT"),
        (2, "PAID"),
        (3, "IN_QUEUE"),
        (4, "IN_PROGRESS"),
        (5, "COMPLETED"),
    ]


async def test_reconnecting_with_last_event_id_replays_only_what_was_missed(client, db):
    order = (await post_order(client, order_body())).json()
    await _deliver(API_2, order)
    await wait_for_status(db, order["id"], "COMPLETED")
    async with Stream(API_2, order, last_event_id=2) as stream:
        await stream.wait_for("COMPLETED")
        await asyncio.sleep(0.3)
    assert [seq for seq, _, _ in stream.events] == [3, 4, 5]


async def test_stream_needs_authorization(client):
    order = (await post_order(client, order_body())).json()
    async with httpx.AsyncClient(base_url=API_1, timeout=10) as http:
        no_token = await http.get(f"/api/v1/orders/{order['id']}/events")
        bad_token = await http.get(
            f"/api/v1/orders/{order['id']}/events", params={"token": "99999999999.bad"}
        )
    assert no_token.status_code == bad_token.status_code == 404


async def test_tracking_token_is_bound_to_one_order(client):
    first = (await post_order(client, order_body())).json()
    second = (await post_order(client, order_body())).json()
    async with httpx.AsyncClient(base_url=API_1, timeout=10) as http:
        response = await http.get(
            f"/api/v1/orders/{second['id']}/events", params={"token": first["tracking_token"]}
        )
    assert response.status_code == 404
