"""Simulated payment provider (spec section 6).

Events go through a SQLite outbox, so webhook retries continue after this container is killed,
the way a real gateway keeps retrying.
"""

import asyncio
import csv
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

DB_PATH = os.environ.get("DB_PATH", "/data/mock-pay.db")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "http://proxy/api/v1/webhooks/payment")
SECRET = os.environ["PAYMENT_WEBHOOK_SECRET"]
MAX_DELIVERY_ATTEMPTS = 8
SLOW_WEBHOOK_SECONDS = 60
ORDER_ID_PATTERN = re.compile(r"ORD-[0-9]{13}-[0-9]{3}")

ACTIONS = {
    "success": "จ่ายสำเร็จ",
    "fail": "จ่ายไม่สำเร็จ",
    "duplicate": "จ่ายสำเร็จแต่ส่ง webhook ซ้ำ 5 ครั้ง",
    "slow": "จ่ายสำเร็จแต่ส่ง webhook ช้า 60 วินาที",
    "wrong_amount": "จ่ายยอดไม่ตรง",
}

db = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=FULL")
db.execute(
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        type TEXT NOT NULL,
        amount_satang INTEGER NOT NULL,
        occurred_at TEXT NOT NULL,
        body TEXT NOT NULL,
        copies INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at REAL NOT NULL,
        refund_reference TEXT UNIQUE
    )
    """
)
db.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS one_charge_per_order ON events (order_id) "
    "WHERE type = 'payment.succeeded'"
)


def _enqueue(
    order_id: str,
    kind: str,
    amount: int,
    *,
    copies: int = 1,
    delay_seconds: int = 0,
    refund_reference: str | None = None,
) -> bool:
    event_id = f"evt_{secrets.token_hex(12)}"
    occurred_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = json.dumps(
        {
            "event_id": event_id,
            "type": kind,
            "order_id": order_id,
            "amount_satang": amount,
            "currency": "THB",
            "occurred_at": occurred_at,
        }
    )
    try:
        db.execute(
            """
            INSERT INTO events (event_id, order_id, type, amount_satang, occurred_at, body, copies,
                                next_attempt_at, refund_reference)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                order_id,
                kind,
                amount,
                occurred_at,
                body,
                copies,
                time.time() + delay_seconds,
                refund_reference,
            ),
        )
    except sqlite3.IntegrityError:
        return False  # already charged, or this refund reference was already processed
    return True


def _sign(body: bytes) -> str:
    stamp = int(time.time())
    digest = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


async def _post_once(client: httpx.AsyncClient, body: bytes) -> bool:
    try:
        response = await client.post(
            WEBHOOK_URL,
            content=body,
            headers={"Content-Type": "application/json", "X-IBM-Signature": _sign(body)},
        )
    except httpx.HTTPError:
        return False
    return response.is_success


async def _deliver(client: httpx.AsyncClient, row: tuple, in_flight: set[str]) -> None:
    event_id, body, copies, attempts = row
    try:
        raw = body.encode()
        results = await asyncio.gather(*(_post_once(client, raw) for _ in range(copies)))
        attempts += 1
        if any(results):
            db.execute(
                "UPDATE events SET status = 'delivered', attempts = ? WHERE event_id = ?",
                (attempts, event_id),
            )
        elif attempts >= MAX_DELIVERY_ATTEMPTS:
            db.execute(
                "UPDATE events SET status = 'failed', attempts = ? WHERE event_id = ?",
                (attempts, event_id),
            )
        else:
            db.execute(
                "UPDATE events SET attempts = ?, next_attempt_at = ? WHERE event_id = ?",
                (attempts, time.time() + 2 ** (attempts - 1), event_id),
            )
    finally:
        in_flight.discard(event_id)


async def _delivery_loop() -> None:
    in_flight: set[str] = set()
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            rows = db.execute(
                "SELECT event_id, body, copies, attempts FROM events "
                "WHERE status = 'pending' AND next_attempt_at <= ?",
                (time.time(),),
            ).fetchall()
            for row in rows:
                if row[0] not in in_flight:
                    in_flight.add(row[0])
                    asyncio.create_task(_deliver(client, row, in_flight))
            await asyncio.sleep(0.2)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(_delivery_loop())
    yield
    task.cancel()


app = FastAPI(title="mock-pay", lifespan=lifespan)


def _valid_order(order_id: str) -> str:
    if ORDER_ID_PATTERN.fullmatch(order_id) is None:
        raise HTTPException(status_code=404)
    return order_id


@app.get("/checkout/{order_id}", response_class=HTMLResponse)
async def checkout_page(order_id: str, amount: int = Query(gt=0)) -> str:
    safe_id = html.escape(_valid_order(order_id))
    baht = f"{amount // 100:,}.{amount % 100:02d}"
    buttons = "".join(
        f'<form method="post" action="/checkout/{safe_id}/{action}?amount={amount}">'
        f"<button>{label}</button></form>"
        for action, label in ACTIONS.items()
    )
    return (
        '<!doctype html><html lang="th"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Mock Payment</title>"
        "<style>body{font-family:sans-serif;max-width:420px;margin:2rem auto;padding:0 1rem}"
        "button{width:100%;padding:.75rem;margin:.25rem 0;font-size:1rem;cursor:pointer}</style>"
        f"<h1>ชำระเงินจำลอง</h1><p>ออเดอร์ {safe_id}</p><p>ยอด ฿{baht}</p>{buttons}</html>"
    )


@app.post("/checkout/{order_id}/{action}", response_class=HTMLResponse)
async def pay(order_id: str, action: str, amount: int = Query(gt=0)) -> str:
    _valid_order(order_id)
    if action == "success":
        accepted = _enqueue(order_id, "payment.succeeded", amount)
    elif action == "fail":
        accepted = _enqueue(order_id, "payment.failed", amount)
    elif action == "duplicate":
        accepted = _enqueue(order_id, "payment.succeeded", amount, copies=5)
    elif action == "slow":
        accepted = _enqueue(
            order_id, "payment.succeeded", amount, delay_seconds=SLOW_WEBHOOK_SECONDS
        )
    elif action == "wrong_amount":
        accepted = _enqueue(order_id, "payment.succeeded", max(amount - 100, 1))
    else:
        raise HTTPException(status_code=404)
    message = "รับรายการแล้ว" if accepted else "ออเดอร์นี้ชำระเงินไปแล้ว"
    return f'<!doctype html><html lang="th"><meta charset="utf-8"><p>{message}</p></html>'


class RefundRequest(BaseModel):
    reference: str = Field(min_length=1, max_length=64)
    order_id: str
    amount_satang: int = Field(gt=0)


@app.post("/refunds")
async def refund(body: RefundRequest) -> dict[str, object]:
    """Idempotent on reference, like a real gateway's refund API."""
    created = _enqueue(
        body.order_id, "payment.refunded", body.amount_satang, refund_reference=body.reference
    )
    return {"reference": body.reference, "created": created}


@app.get("/settlement.csv", response_class=PlainTextResponse)
async def settlement() -> str:
    """Settlement file in the format POST /admin/reconciliation expects."""
    rows = db.execute(
        "SELECT event_id, order_id, type, amount_satang, occurred_at FROM events "
        "WHERE type != 'payment.failed' ORDER BY occurred_at, event_id"
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["event_id", "order_id", "type", "amount_satang", "settled_at"])
    writer.writerows(rows)
    return output.getvalue()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
