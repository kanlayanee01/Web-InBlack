"""Simulated game top-up system (spec section 7).

It deliberately has NO idempotency: POSTing the same reference twice tops up twice. State is kept
in SQLite on a volume, so /stats still tells the truth after this container is killed.
"""

import asyncio
import json
import os
import random
import sqlite3

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "/data/mock-game.db")
HANG_MS = int(os.environ.get("HANG_MS", "30000"))
TEST_MODE = os.environ.get("APP_ENV") == "test"

config = {
    "FAIL_RATE": float(os.environ.get("FAIL_RATE", "0")),
    "TIMEOUT_RATE": float(os.environ.get("TIMEOUT_RATE", "0")),
    "SUCCESS_BUT_TIMEOUT_RATE": float(os.environ.get("SUCCESS_BUT_TIMEOUT_RATE", "0")),
    "LATENCY_MS": float(os.environ.get("LATENCY_MS", "0")),
}

db = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA synchronous=FULL")
db.execute(
    """
    CREATE TABLE IF NOT EXISTS topups (
        reference TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        game_uid TEXT NOT NULL,
        sku TEXT NOT NULL,
        qty INTEGER NOT NULL,
        post_count INTEGER NOT NULL DEFAULT 0,
        success_count INTEGER NOT NULL DEFAULT 0
    )
    """
)
# A request in flight when the process died never applied, because applying is a single write.
db.execute("UPDATE topups SET status = 'FAILED' WHERE status = 'PROCESSING'")

app = FastAPI(title="mock-game")


class TopupRequest(BaseModel):
    reference: str
    game_uid: str
    sku: str
    qty: int = 1


def _set_status(reference: str, status: str) -> None:
    db.execute("UPDATE topups SET status = ? WHERE reference = ?", (status, reference))


async def _latency() -> None:
    await asyncio.sleep(config["LATENCY_MS"] / 1000)


async def _hang() -> None:
    await asyncio.sleep(HANG_MS / 1000)


@app.post("/topups")
async def create_topup(body: TopupRequest, request: Request) -> dict[str, object]:
    print(
        json.dumps(
            {
                "service": "mock-game",
                "message": "topup received",
                "request_id": request.headers.get("x-request-id", "-"),
                "reference": body.reference,
            }
        ),
        flush=True,
    )
    # Recorded on arrival, so a GET during a slow POST answers PROCESSING instead of "not found".
    db.execute(
        """
        INSERT INTO topups (reference, status, game_uid, sku, qty, post_count)
        VALUES (?, 'PROCESSING', ?, ?, ?, 1)
        ON CONFLICT (reference) DO UPDATE SET status = 'PROCESSING', post_count = post_count + 1
        """,
        (body.reference, body.game_uid, body.sku, body.qty),
    )
    await _latency()

    roll = random.random()
    fail_below = config["FAIL_RATE"]
    timeout_below = fail_below + config["TIMEOUT_RATE"]
    success_timeout_below = timeout_below + config["SUCCESS_BUT_TIMEOUT_RATE"]

    if roll < fail_below:
        _set_status(body.reference, "FAILED")
        raise HTTPException(status_code=503, detail="upstream failure")
    if roll < timeout_below:
        _set_status(body.reference, "FAILED")
        await _hang()
        raise HTTPException(status_code=504, detail="timed out")

    db.execute(
        "UPDATE topups SET status = 'SUCCEEDED', success_count = success_count + 1 "
        "WHERE reference = ?",
        (body.reference,),
    )
    if roll < success_timeout_below:
        await _hang()
    return {"reference": body.reference, "status": "SUCCEEDED"}


@app.get("/topups/{reference}")
async def get_topup(reference: str) -> dict[str, object]:
    await _latency()
    row = db.execute(
        "SELECT reference, status, game_uid, sku, qty, success_count FROM topups "
        "WHERE reference = ?",
        (reference,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown reference")
    keys = ("reference", "status", "game_uid", "sku", "qty", "success_count")
    return dict(zip(keys, row, strict=True))


@app.get("/stats")
async def stats() -> dict[str, int]:
    """Successful top-ups per reference. Any value above 1 is a double top-up."""
    rows = db.execute("SELECT reference, success_count FROM topups WHERE success_count > 0")
    return dict(rows.fetchall())


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/_test/config")
async def set_config(update: dict[str, float]) -> dict[str, float]:
    """Changes failure behaviour without a restart. Only exists when APP_ENV=test."""
    if not TEST_MODE:
        raise HTTPException(status_code=404)
    unknown = set(update) - set(config)
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown keys: {sorted(unknown)}")
    config.update(update)
    return config
