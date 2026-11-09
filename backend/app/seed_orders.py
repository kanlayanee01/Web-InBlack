"""Loads a large volume of orders for performance testing.

    docker compose exec api-1 python -m app.seed_orders 100000
"""

import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta

import asyncpg

from app.domain.order_number import generate_order_number
from app.settings import get_settings

STATUSES = (
    ("COMPLETED", 70),
    ("EXPIRED", 12),
    ("REFUNDED", 5),
    ("PAYMENT_FAILED", 5),
    ("PENDING_PAYMENT", 3),
    ("IN_QUEUE", 2),
    ("FAILED", 2),
    ("NEEDS_REVIEW", 1),
)
HISTORY_DAYS = 180
PRICE_SATANG = 34_900
COLUMNS = (
    "id",
    "email",
    "game_uid",
    "payment_method",
    "subtotal_satang",
    "discount_satang",
    "net_satang",
    "fee_satang",
    "total_satang",
    "vat_satang",
    "paid_satang",
    "status",
    "holds_inventory",
    "expires_at",
    "created_at",
    "updated_at",
)


def _rows(count: int) -> list[tuple[object, ...]]:
    statuses = [name for name, weight in STATUSES for _ in range(weight)]
    now = datetime.now(UTC)
    seen: set[str] = set()
    rows: list[tuple[object, ...]] = []
    while len(rows) < count:
        order_id = generate_order_number()
        if order_id in seen:
            continue
        seen.add(order_id)
        status = random.choice(statuses)
        created = now - timedelta(seconds=random.randrange(HISTORY_DAYS * 86_400))
        paid = PRICE_SATANG if status in ("COMPLETED", "REFUNDED", "IN_QUEUE", "FAILED") else 0
        rows.append(
            (
                order_id,
                f"load{random.randrange(20_000)}@example.com",
                f"{random.randrange(10**8, 10**9)}",
                "promptpay",
                PRICE_SATANG,
                0,
                PRICE_SATANG,
                0,
                PRICE_SATANG,
                2_283,
                paid,
                status,
                False,
                created + timedelta(minutes=15),
                created,
                created,
            )
        )
    return rows


async def main(count: int) -> None:
    connection = await asyncpg.connect(get_settings().database_url)
    try:
        await connection.copy_records_to_table("orders", records=_rows(count), columns=COLUMNS)
        await connection.execute("ANALYZE orders")
    finally:
        await connection.close()
    print(f"inserted {count} orders")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000))
