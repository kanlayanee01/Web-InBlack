"""Background worker: top-ups, provider refunds, order expiry and housekeeping.

Run with `python -m app.worker`. It holds no state of its own, so killing it at any moment is safe.
"""

import asyncio
import contextlib
import json
import logging
import secrets

import asyncpg
import httpx

from app.services import fulfillment, jobs, orders
from app.settings import Settings, get_settings

log = logging.getLogger("worker")

POLL_SECONDS = 0.2
SCHEDULER_SECONDS = 3
HEARTBEAT_SECONDS = 5
FULFILL_BACKOFF_BASE_MS = 1_000
FULFILL_BACKOFF_CAP_MS = 16_000
REFUND_BACKOFF_BASE_MS = 5_000
REFUND_BACKOFF_CAP_MS = 60_000
TRANSIENT_ERRORS = (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError)


class Worker:
    def __init__(
        self,
        settings: Settings,
        pool: asyncpg.Pool,
        game: fulfillment.GameClient,
        pay: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.pool = pool
        self.game = game
        self.pay = pay
        self.id = f"worker-{secrets.token_hex(4)}"

    async def _handle_fulfill(self, job: asyncpg.Record) -> None:
        order_id = _payload(job)["order_id"]
        final = job["attempts"] >= job["max_attempts"]
        verdict, error = await fulfillment.fulfill_order(self.pool, self.game, order_id, final)
        if verdict is fulfillment.Verdict.FINISHED:
            await jobs.complete(self.pool, job["id"], self.id)
        elif verdict is fulfillment.Verdict.EXHAUSTED:
            await jobs.bury(self.pool, job["id"], self.id, error)
        else:
            delay = jobs.backoff_ms(
                job["attempts"], FULFILL_BACKOFF_BASE_MS, FULFILL_BACKOFF_CAP_MS
            )
            await jobs.retry_later(self.pool, job["id"], self.id, delay, error)

    async def _handle_refund(self, job: asyncpg.Record) -> None:
        payload = _payload(job)
        refund = await self.pool.fetchrow(
            "SELECT id, order_id, amount_satang, status FROM refunds WHERE id = $1::uuid",
            payload["refund_id"],
        )
        if refund is None or refund["status"] != "PENDING":
            await jobs.complete(self.pool, job["id"], self.id)
            return
        error = await self._send_refund(refund)
        if error is None:
            # The refund becomes SUCCEEDED when the provider's payment.refunded webhook arrives.
            await jobs.complete(self.pool, job["id"], self.id)
        elif job["attempts"] >= job["max_attempts"]:
            await jobs.bury(self.pool, job["id"], self.id, error)
        else:
            delay = jobs.backoff_ms(job["attempts"], REFUND_BACKOFF_BASE_MS, REFUND_BACKOFF_CAP_MS)
            await jobs.retry_later(self.pool, job["id"], self.id, delay, error)

    async def _send_refund(self, refund: asyncpg.Record) -> str | None:
        try:
            response = await self.pay.post(
                "/refunds",
                json={
                    "reference": str(refund["id"]),
                    "order_id": refund["order_id"],
                    "amount_satang": refund["amount_satang"],
                },
            )
        except httpx.HTTPError as error:
            return type(error).__name__
        return None if response.is_success else f"provider returned {response.status_code}"

    async def _keep_lease(self, job_id: int) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            with contextlib.suppress(*TRANSIENT_ERRORS):
                await jobs.extend_lease(self.pool, job_id, self.id)

    async def _run_job(self, job: asyncpg.Record) -> None:
        heartbeat = asyncio.create_task(self._keep_lease(job["id"]))
        try:
            if job["kind"] == "fulfill_order":
                await self._handle_fulfill(job)
            elif job["kind"] == "refund_provider":
                await self._handle_refund(job)
            else:
                await jobs.bury(self.pool, job["id"], self.id, f"unknown job kind {job['kind']}")
        finally:
            heartbeat.cancel()

    async def consume(self) -> None:
        while True:
            try:
                job = await jobs.claim(self.pool, self.id)
                if job is None:
                    await asyncio.sleep(POLL_SECONDS)
                    continue
                await self._run_job(job)
            except TRANSIENT_ERRORS:
                # The job's lease runs out and the reaper hands it back.
                log.warning("database unavailable, waiting")
                await asyncio.sleep(1)
            except Exception:
                log.exception("job failed unexpectedly")
                await asyncio.sleep(1)

    async def schedule(self) -> None:
        while True:
            try:
                await jobs.reap_expired_leases(self.pool)
                await orders.expire_due_orders(self.pool)
                await fulfillment.flag_stuck_orders(self.pool)
                await self.pool.execute("DELETE FROM idempotency_keys WHERE expires_at <= now()")
            except TRANSIENT_ERRORS:
                log.warning("database unavailable, waiting")
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(SCHEDULER_SECONDS)


def _payload(job: asyncpg.Record) -> dict[str, str]:
    payload: dict[str, str] = json.loads(job["payload"])
    return payload


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    concurrency = settings.worker_concurrency
    # One connection per job for its advisory lock, one for its queries, plus the scheduler.
    pool = await asyncpg.create_pool(
        settings.database_url, min_size=0, max_size=concurrency * 2 + 2
    )
    timeout = httpx.Timeout(settings.game_timeout_seconds)
    async with (
        httpx.AsyncClient(base_url=settings.mock_game_url, timeout=timeout) as game_http,
        httpx.AsyncClient(base_url=settings.mock_pay_url, timeout=timeout) as pay_http,
    ):
        worker = Worker(settings, pool, fulfillment.GameClient(game_http), pay_http)
        await asyncio.gather(worker.schedule(), *(worker.consume() for _ in range(concurrency)))


if __name__ == "__main__":
    asyncio.run(main())
