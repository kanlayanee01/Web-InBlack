"""Customer sign-in, order lookup and the real-time status stream."""

import asyncio
import hmac
import json
import time
from collections.abc import AsyncIterator

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api import deps
from app.api.schemas import Email
from app.domain.masking import mask_game_uid
from app.domain.order_number import is_valid_order_number
from app.domain.state_machine import BADGE_BY_STATUS, OrderStatus
from app.problem import Problem, dumps
from app.services import auth, orders
from app.settings import get_settings

router = APIRouter(prefix="/api/v1")

JSON = "application/json"
TRACK_RESPONSE_FLOOR_SECONDS = 0.25
HEARTBEAT_EVERY_POLLS = 15
FALLBACK_POLL_SECONDS = 1
CURRENT_STATUSES = ["PENDING_PAYMENT", "PAID", "IN_QUEUE", "IN_PROGRESS", "NEEDS_REVIEW"]
MY_ORDERS_LIMIT = 50


class OtpRequest(BaseModel):
    email: Email


class OtpVerify(BaseModel):
    email: Email
    code: str = Field(pattern=r"^[0-9]{6}$")


class TrackRequest(BaseModel):
    order_id: str = Field(max_length=64)
    email: str = Field(max_length=254)


def _require_valid_id(order_id: str) -> None:
    if not is_valid_order_number(order_id):
        raise Problem(400, "INVALID_ORDER_ID", "This order number is not valid")


@router.post("/auth/otp/request", status_code=202)
async def otp_request(body: OtpRequest, request: Request) -> Response:
    limiter = request.app.state.limiter
    await limiter.enforce(f"otp-ip:{deps.client_ip(request)}", 20, 60)
    await limiter.enforce(f"otp-email:{body.email}", 5, 60)
    await auth.request_otp(request.app.state.db, get_settings(), body.email)
    return Response(dumps({"sent": True}), status_code=202, media_type=JSON)


@router.post("/auth/otp/verify")
async def otp_verify(body: OtpVerify, request: Request) -> Response:
    await request.app.state.limiter.enforce(f"otp-verify:{deps.client_ip(request)}", 30, 60)
    pool = request.app.state.db
    customer_id = await auth.verify_otp(pool, get_settings(), body.email, body.code)
    lifetime = auth.CUSTOMER_SESSION_DAYS * 86400
    token, csrf_token = await auth.create_session(pool, "customer", customer_id, lifetime)
    response = Response(dumps({"email": body.email, "csrf_token": csrf_token}), media_type=JSON)
    deps.set_session_cookie(response, deps.CUSTOMER_COOKIE, token, lifetime, "lax")
    return response


@router.get("/auth/me")
async def me(session: asyncpg.Record = Depends(deps.require_customer)) -> Response:
    return Response(
        dumps({"email": session["customer_email"], "csrf_token": session["csrf_token"]}),
        media_type=JSON,
    )


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request, _: asyncpg.Record = Depends(deps.require_customer)
) -> Response:
    await auth.delete_session(request.app.state.db, request.cookies[deps.CUSTOMER_COOKIE])
    response = Response(status_code=204)
    response.delete_cookie(deps.CUSTOMER_COOKIE, path="/", secure=True, httponly=True)
    return response


@router.get("/me/orders")
async def my_orders(
    request: Request,
    tab: str = Query(default="current", pattern="^(current|past)$"),
    session: asyncpg.Record = Depends(deps.require_customer),
) -> Response:
    operator = "= ANY" if tab == "current" else "<> ALL"
    rows = await request.app.state.db.fetch(
        f"""
        SELECT id, status, total_satang, created_at FROM orders
        WHERE email = $1 AND status {operator}($2::text[])
        ORDER BY created_at DESC, id DESC
        LIMIT $3
        """,
        session["customer_email"],
        CURRENT_STATUSES,
        MY_ORDERS_LIMIT,
    )
    items = [
        {
            "id": row["id"],
            "status": row["status"],
            "badge": BADGE_BY_STATUS[OrderStatus(row["status"])].value,
            "total_satang": row["total_satang"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]
    return Response(dumps({"items": items}), media_type=JSON)


async def _timeline(pool: asyncpg.Pool, order_id: str) -> list[dict[str, object]]:
    rows = await pool.fetch(
        "SELECT seq, to_status, created_at FROM order_events WHERE order_id = $1 ORDER BY seq",
        order_id,
    )
    return [_event_json(row) for row in rows]


def _event_json(row: asyncpg.Record) -> dict[str, object]:
    status = OrderStatus(row["to_status"])
    return {
        "seq": row["seq"],
        "status": status.value,
        "badge": BADGE_BY_STATUS[status].value,
        "created_at": row["created_at"].isoformat(),
    }


async def _can_view(request: Request, order: asyncpg.Record) -> bool:
    customer = await deps.optional_customer(request)
    if customer is not None and customer["customer_email"] == order["email"]:
        return True
    return await deps.optional_admin(request) is not None


@router.get("/orders/{order_id}")
async def get_order(order_id: str, request: Request) -> Response:
    _require_valid_id(order_id)
    pool = request.app.state.db
    order = await pool.fetchrow("SELECT email FROM orders WHERE id = $1", order_id)
    # 404 rather than 403, so a stranger cannot learn that the order exists.
    if order is None or not await _can_view(request, order):
        raise Problem(404, "ORDER_NOT_FOUND", "Order not found")
    settings = get_settings()
    async with pool.acquire() as conn:
        payload = await orders.fetch_order_json(conn, order_id, settings.mock_pay_public_url)
    assert payload is not None
    payload["timeline"] = await _timeline(pool, order_id)
    payload["tracking_token"] = auth.make_tracking_token(settings.session_secret, order_id)
    return Response(dumps(payload), media_type=JSON)


async def _hold_until(started: float) -> None:
    remaining = TRACK_RESPONSE_FLOOR_SECONDS - (time.monotonic() - started)
    if remaining > 0:
        await asyncio.sleep(remaining)


@router.post("/orders/track")
async def track_order(body: TrackRequest, request: Request) -> Response:
    limiter = request.app.state.limiter
    await limiter.enforce(f"track-ip:{deps.client_ip(request)}", 10, 60)
    await limiter.enforce(f"track-order:{body.order_id}", 5, 60)
    _require_valid_id(body.order_id)

    # A fixed minimum response time, and one message for "no such order" and "wrong email".
    started = time.monotonic()
    pool = request.app.state.db
    settings = get_settings()
    order = await pool.fetchrow("SELECT email FROM orders WHERE id = $1", body.order_id)
    supplied = body.email.strip().lower().encode()
    actual = order["email"].encode() if order is not None else b""
    matched = hmac.compare_digest(supplied, actual) and order is not None

    payload = None
    if matched:
        async with pool.acquire() as conn:
            payload = await orders.fetch_order_json(
                conn, body.order_id, settings.mock_pay_public_url
            )
    await _hold_until(started)
    if payload is None:
        raise Problem(404, "ORDER_NOT_FOUND", "No order matches this number and email")

    payload["game_uid"] = mask_game_uid(str(payload["game_uid"]))
    payload["timeline"] = await _timeline(pool, body.order_id)
    payload["tracking_token"] = auth.make_tracking_token(settings.session_secret, body.order_id)
    return Response(dumps(payload), media_type=JSON)


def _parse_last_event_id(value: str | None) -> int:
    if value is None or not value.isascii() or not value.isdigit():
        return 0
    return int(value)


async def _event_stream(request: Request, order_id: str, last_seq: int) -> AsyncIterator[bytes]:
    pool: asyncpg.Pool = request.app.state.db
    hub = request.app.state.hub
    waiter = hub.subscribe(order_id)
    idle_polls = 0
    try:
        yield b"retry: 1000\n\n"
        while True:
            # Cleared before reading, so an event committed after the read still wakes us.
            waiter.clear()
            try:
                rows = await pool.fetch(
                    """
                    SELECT seq, to_status, created_at FROM order_events
                    WHERE order_id = $1 AND seq > $2 ORDER BY seq
                    """,
                    order_id,
                    last_seq,
                )
            except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError):
                rows = []
            for row in rows:
                last_seq = row["seq"]
                data = json.dumps(_event_json(row), ensure_ascii=False, separators=(",", ":"))
                yield f"id: {last_seq}\nevent: status\ndata: {data}\n\n".encode()
                idle_polls = 0
            try:
                await asyncio.wait_for(waiter.wait(), FALLBACK_POLL_SECONDS)
            except TimeoutError:
                idle_polls += 1
                if idle_polls >= HEARTBEAT_EVERY_POLLS:
                    idle_polls = 0
                    yield b": keep-alive\n\n"
    finally:
        hub.unsubscribe(order_id, waiter)


@router.get("/orders/{order_id}/events")
async def order_events(
    order_id: str, request: Request, token: str | None = Query(default=None, max_length=128)
) -> StreamingResponse:
    _require_valid_id(order_id)
    order = await request.app.state.db.fetchrow("SELECT email FROM orders WHERE id = $1", order_id)
    authorized = order is not None and (
        (
            token is not None
            and auth.tracking_token_valid(get_settings().session_secret, order_id, token)
        )
        or await _can_view(request, order)
    )
    if not authorized:
        raise Problem(404, "ORDER_NOT_FOUND", "Order not found")
    last_seq = _parse_last_event_id(request.headers.get("last-event-id"))
    return StreamingResponse(
        _event_stream(request, order_id, last_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
