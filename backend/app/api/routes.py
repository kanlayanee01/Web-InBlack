import time

import asyncpg
from fastapi import APIRouter, Header, Request, Response
from pydantic import ValidationError

from app.api.schemas import CreateOrderRequest, PaymentWebhook, QuoteRequest
from app.domain.errors import DomainError
from app.problem import Problem, dumps, from_domain
from app.seed import seed
from app.services import idempotency, orders, payments
from app.settings import get_settings

router = APIRouter(prefix="/api/v1")

JSON = "application/json"
ORDER_SCOPE = "orders:create"
RESETTABLE_TABLES = (
    "reconciliation_items, reconciliation_runs, audit_logs, sessions, otp_codes, admin_users, "
    "idempotency_keys, jobs, fulfillment_attempts, fulfillments, ledger_entries, refunds, "
    "webhook_events, voucher_redemptions, order_events, order_items, orders, customers, vouchers, "
    "product_email_usage, products"
)


def _product_json(row: asyncpg.Record) -> dict[str, object]:
    return {
        "sku": row["sku"],
        "slug": row["slug"],
        "name": row["name"],
        "description_th": row["description_th"],
        "description_en": row["description_en"],
        "price_satang": row["price_satang"],
        "stock_remaining": row["stock_remaining"],
        "max_qty_per_order": row["max_qty_per_order"],
        "max_qty_per_email": row["max_qty_per_email"],
    }


def _items(body: QuoteRequest) -> list[orders.RequestedItem]:
    return [orders.RequestedItem(item.sku, item.qty) for item in body.items]


@router.get("/products")
async def list_products(request: Request) -> Response:
    rows = await request.app.state.db.fetch(
        "SELECT * FROM products WHERE is_active ORDER BY price_satang"
    )
    return Response(
        dumps({"items": [_product_json(row) for row in rows]}),
        media_type=JSON,
        headers={"Cache-Control": "public, max-age=5"},
    )


@router.get("/products/{slug}")
async def get_product(slug: str, request: Request) -> Response:
    row = await request.app.state.db.fetchrow(
        "SELECT * FROM products WHERE slug = $1 AND is_active", slug
    )
    if row is None:
        raise Problem(404, "PRODUCT_NOT_FOUND", "Product not found")
    return Response(
        dumps(_product_json(row)),
        media_type=JSON,
        headers={"Cache-Control": "public, max-age=5"},
    )


@router.post("/orders/quote")
async def quote_order(body: QuoteRequest, request: Request) -> Response:
    """Prices a cart with the same code path as order creation, and changes nothing."""
    async with request.app.state.db.acquire() as conn:
        try:
            quote = await orders.build_quote(
                conn, _items(body), body.payment_method, body.voucher_code
            )
        except DomainError as error:
            raise from_domain(error) from error
    price = quote.price
    return Response(
        dumps(
            {
                "subtotal_satang": price.subtotal_satang,
                "discount_satang": price.discount_satang,
                "net_satang": price.net_satang,
                "fee_satang": price.fee_satang,
                "total_satang": price.total_satang,
                "vat_satang": price.vat_satang,
            }
        ),
        media_type=JSON,
    )


@router.post("/orders", status_code=201)
async def create_order(
    request: Request, idempotency_key: str | None = Header(default=None)
) -> Response:
    key = idempotency.require_key(idempotency_key)
    raw_body = await request.body()
    body = CreateOrderRequest.model_validate_json(raw_body)
    settings = get_settings()
    new_order = orders.NewOrder(
        items=_items(body),
        email=body.email,
        game_uid=body.game_uid,
        payment_method=body.payment_method,
        voucher_code=body.voucher_code,
    )

    async def handler(conn: asyncpg.Connection) -> tuple[int, bytes]:
        order_id = await orders.create_order(conn, new_order, settings.order_ttl_seconds)
        payload = await orders.fetch_order_json(conn, order_id, settings.mock_pay_public_url)
        return 201, dumps(payload)

    status, response_body = await idempotency.run_once(
        request.app.state.db, ORDER_SCOPE, key, raw_body, handler
    )
    media_type = JSON if status < 400 else "application/problem+json"
    return Response(response_body, status_code=status, media_type=media_type)


@router.post("/webhooks/payment")
async def payment_webhook(
    request: Request, x_ibm_signature: str | None = Header(default=None)
) -> Response:
    # Verified against the bytes as received, before any JSON parsing.
    raw_body = await request.body()
    payments.verify_signature(
        raw_body, x_ibm_signature, get_settings().payment_webhook_secret, int(time.time())
    )
    try:
        body = PaymentWebhook.model_validate_json(raw_body)
    except ValidationError as error:
        raise Problem(400, "INVALID_WEBHOOK_BODY", "Webhook body is not valid") from error
    event = payments.PaymentEvent(
        event_id=body.event_id,
        type=body.type,
        order_id=body.order_id,
        amount_satang=body.amount_satang,
        currency=body.currency.upper(),
        occurred_at=body.occurred_at,
    )
    outcome = await payments.handle_event(request.app.state.db, event, raw_body.decode())
    return Response(dumps({"received": True, "outcome": outcome}), media_type=JSON)


@router.post("/_test/reset")
async def test_reset(request: Request) -> Response:
    settings = get_settings()
    if settings.app_env != "test":
        raise Problem(404, "NOT_FOUND", "Not found")
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await conn.execute(f"TRUNCATE {RESETTABLE_TABLES} RESTART IDENTITY CASCADE")
        await seed(conn, settings.seed_admin_password)
    return Response(dumps({"reset": True}), media_type=JSON)
