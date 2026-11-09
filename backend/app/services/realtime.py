"""Wakes SSE connections on this replica when any replica or the worker commits an order event.

PostgreSQL NOTIFY carries the signal; the data always comes from order_events, so a missed signal
can only delay an update (each stream also re-checks every second) and can never lose one.
"""

import asyncio
import logging

import asyncpg

log = logging.getLogger("realtime")
CHANNEL = "order_events"
RECONNECT_SECONDS = 1


class OrderEventHub:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._waiters: dict[str, set[asyncio.Event]] = {}

    def subscribe(self, order_id: str) -> asyncio.Event:
        waiter = asyncio.Event()
        self._waiters.setdefault(order_id, set()).add(waiter)
        return waiter

    def unsubscribe(self, order_id: str, waiter: asyncio.Event) -> None:
        waiters = self._waiters.get(order_id)
        if waiters is None:
            return
        waiters.discard(waiter)
        if not waiters:
            del self._waiters[order_id]

    def _on_notify(self, _conn: object, _pid: int, _channel: str, order_id: str) -> None:
        for waiter in self._waiters.get(order_id, ()):
            waiter.set()

    async def run(self) -> None:
        while True:
            connection = None
            try:
                connection = await asyncpg.connect(self._dsn)
                await connection.add_listener(CHANNEL, self._on_notify)
                while not connection.is_closed():  # noqa: ASYNC110 - asyncpg offers no wait here
                    await asyncio.sleep(RECONNECT_SECONDS)
            except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError):
                log.warning("order event listener lost its connection, retrying")
            finally:
                if connection is not None and not connection.is_closed():
                    await connection.close()
            await asyncio.sleep(RECONNECT_SECONDS)
