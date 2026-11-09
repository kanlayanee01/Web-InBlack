"""Required seed data (spec section 4). Safe to run repeatedly: existing rows are left alone."""

import asyncio

import asyncpg
from argon2 import PasswordHasher

from app.settings import get_settings

# sku, slug, name, price, stock, max per order, max per email
PRODUCTS = [
    ("DIAMOND-1000", "diamond-1000", "1,000 Diamonds", 34900, None, None, None),
    ("DIAMOND-5000", "diamond-5000", "5,000 Diamonds", 159000, None, None, None),
    ("MONTHLY-PASS", "monthly-pass", "Monthly Pass", 17900, None, 1, None),
    ("FLASH-SKIN-01", "flash-skin-01", "Limited Skin (Flash Sale)", 9900, 100, None, 2),
]

# code, kind, percent_bp, max discount, flat, min subtotal, total uses, uses per email
VOUCHERS = [
    ("SAVE10", "percent", 1000, 5000, 0, 20000, 10, 1),
    ("FLAT20", "flat", 0, None, 2000, 10000, None, None),
]

ADMIN_ROLES = ("viewer", "support", "finance", "superadmin")


async def seed(connection: asyncpg.Connection, admin_password: str) -> None:
    async with connection.transaction():
        await connection.executemany(
            """
            INSERT INTO products (sku, slug, name, price_satang, stock_total, stock_remaining,
                                  max_qty_per_order, max_qty_per_email)
            VALUES ($1, $2, $3, $4, $5, $5, $6, $7)
            ON CONFLICT (sku) DO NOTHING
            """,
            PRODUCTS,
        )
        await connection.executemany(
            """
            INSERT INTO vouchers (code, kind, percent_bp, max_discount_satang, flat_satang,
                                  min_subtotal_satang, max_total_uses, max_uses_per_email)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (code) DO NOTHING
            """,
            VOUCHERS,
        )
        hasher = PasswordHasher()
        await connection.executemany(
            """
            INSERT INTO admin_users (email, password_hash, role)
            VALUES ($1, $2, $3)
            ON CONFLICT (email) DO NOTHING
            """,
            [(f"{role}@inblackm.test", hasher.hash(admin_password), role) for role in ADMIN_ROLES],
        )


async def main() -> None:
    settings = get_settings()
    if not settings.seed_admin_password:
        raise SystemExit("SEED_ADMIN_PASSWORD is required")
    connection = await asyncpg.connect(settings.database_url)
    try:
        await seed(connection, settings.seed_admin_password)
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
