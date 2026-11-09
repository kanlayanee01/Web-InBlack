"""Back office API. Every state-changing action writes an audit log entry."""

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field, StrictInt

from app.api import deps
from app.api.schemas import Email
from app.domain.masking import mask_email, mask_game_uid
from app.domain.order_number import is_valid_order_number
from app.domain.state_machine import OrderStatus
from app.problem import Problem, dumps
from app.services import auth, idempotency, inventory, reconciliation, refunds
from app.services.transitions import lock_order, queue_fulfillment

router = APIRouter(prefix="/api/v1/admin")

JSON = "application/json"
DEFAULT_PAGE = 50
MAX_PAGE = 200
PII_MIN_RANK = deps.ROLE_RANK["support"]
_viewer = Depends(deps.require_role("viewer"))
_support = Depends(deps.require_role("support"))
_finance = Depends(deps.require_role("finance"))
_superadmin = Depends(deps.require_role("superadmin"))


class AdminLogin(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=256)


class RefundRequest(BaseModel):
    amount_satang: StrictInt = Field(gt=0, le=10**12)
    reason: str = Field(min_length=1, max_length=500)


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_satang: StrictInt | None = Field(default=None, ge=0, le=10**12)
    is_active: bool | None = None


class VoucherPatch(BaseModel):
    is_active: bool


class AdminCreate(BaseModel):
    email: Email
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(pattern="^(viewer|support|finance|superadmin)$")


def _actor(session: asyncpg.Record) -> str:
    return f"admin:{session['admin_email']}"


def _sees_pii(session: asyncpg.Record) -> bool:
    return bool(deps.ROLE_RANK[session["role"]] >= PII_MIN_RANK)


def _email_for(session: asyncpg.Record, email: str) -> str:
    return email if _sees_pii(session) else mask_email(email)


def _uid_for(session: asyncpg.Record, uid: str) -> str:
    return uid if _sees_pii(session) else mask_game_uid(uid)


async def _audit(
    conn: asyncpg.Connection, actor: str, action: str, target: str, detail: dict[str, object]
) -> None:
    await conn.execute(
        "INSERT INTO audit_logs (actor, action, target, detail) VALUES ($1, $2, $3, $4::jsonb)",
        actor,
        action,
        target,
        json.dumps(detail, ensure_ascii=False),
    )


def _encode_cursor(created_at: datetime, row_id: object) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        created, row_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(created), str(row_id)
    except (ValueError, TypeError, binascii.Error) as error:
        raise Problem(400, "INVALID_CURSOR", "This cursor is not valid") from error


def _parse_time(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise Problem(400, "INVALID_FILTER", f"{name} must be an ISO 8601 date") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@router.post("/auth/login")
async def login(body: AdminLogin, request: Request) -> Response:
    limiter = request.app.state.limiter
    await limiter.enforce(f"admin-login-ip:{deps.client_ip(request)}", 20, 60)
    await limiter.enforce(f"admin-login:{body.email}", 10, 60)
    pool = request.app.state.db
    admin = await auth.verify_admin_password(pool, body.email, body.password)
    lifetime = auth.ADMIN_SESSION_HOURS * 3600
    token, csrf_token = await auth.create_session(pool, "admin", admin["id"], lifetime)
    response = Response(
        dumps({"email": admin["email"], "role": admin["role"], "csrf_token": csrf_token}),
        media_type=JSON,
    )
    deps.set_session_cookie(response, deps.ADMIN_COOKIE, token, lifetime, "strict")
    return response


@router.get("/auth/me")
async def admin_me(session: asyncpg.Record = _viewer) -> Response:
    return Response(
        dumps(
            {
                "email": session["admin_email"],
                "role": session["role"],
                "csrf_token": session["csrf_token"],
            }
        ),
        media_type=JSON,
    )


@router.post("/auth/logout", status_code=204)
async def admin_logout(request: Request, _: asyncpg.Record = _viewer) -> Response:
    await auth.delete_session(request.app.state.db, request.cookies[deps.ADMIN_COOKIE])
    response = Response(status_code=204)
    response.delete_cookie(deps.ADMIN_COOKIE, path="/", secure=True, httponly=True)
    return response


@router.get("/orders")
async def list_orders(
    request: Request,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = Query(default=None, max_length=254),
    cursor: str | None = None,
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    session: asyncpg.Record = _viewer,
) -> Response:
    conditions: list[str] = []
    args: list[object] = []

    def bind(value: object) -> str:
        args.append(value)
        return f"${len(args)}"

    if status is not None:
        if status not in OrderStatus.__members__:
            raise Problem(400, "INVALID_FILTER", "Unknown status")
        conditions.append(f"status = {bind(status)}")
    if date_from is not None:
        conditions.append(f"created_at >= {bind(_parse_time(date_from, 'date_from'))}")
    if date_to is not None:
        conditions.append(f"created_at < {bind(_parse_time(date_to, 'date_to'))}")
    if q:
        term = q.strip().lower()
        # Exact matches only: both use an index, so search stays fast at 100,000 orders.
        if term.startswith("ord-"):
            conditions.append(f"id = {bind(term.upper())}")
        else:
            conditions.append(f"email = {bind(term)}")
    if cursor is not None:
        created_at, order_id = _decode_cursor(cursor)
        first = bind(created_at)
        second = bind(order_id)
        conditions.append(f"(created_at, id) < ({first}, {second})")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await request.app.state.db.fetch(
        f"""
        SELECT id, status, email, game_uid, payment_method, total_satang, created_at
        FROM orders {where}
        ORDER BY created_at DESC, id DESC
        LIMIT {bind(limit + 1)}
        """,
        *args,
    )
    page = rows[:limit]
    next_cursor = (
        _encode_cursor(page[-1]["created_at"], page[-1]["id"]) if len(rows) > limit else None
    )
    items = [
        {
            "id": row["id"],
            "status": row["status"],
            "email": _email_for(session, row["email"]),
            "game_uid": _uid_for(session, row["game_uid"]),
            "payment_method": row["payment_method"],
            "total_satang": row["total_satang"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in page
    ]
    return Response(dumps({"items": items, "next_cursor": next_cursor}), media_type=JSON)


@router.get("/orders/{order_id}")
async def order_detail(
    order_id: str, request: Request, session: asyncpg.Record = _viewer
) -> Response:
    pool = request.app.state.db
    order = await pool.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
    if order is None:
        raise Problem(404, "ORDER_NOT_FOUND", "Order not found")
    items = await pool.fetch(
        "SELECT sku, name, unit_price_satang, qty FROM order_items WHERE order_id = $1 "
        "ORDER BY line_no",
        order_id,
    )
    events = await pool.fetch(
        """
        SELECT seq, from_status, to_status, actor, reason, created_at
        FROM order_events WHERE order_id = $1 ORDER BY seq
        """,
        order_id,
    )
    webhooks = await pool.fetch(
        """
        SELECT event_id, type, amount_satang, currency, outcome, occurred_at, received_at
        FROM webhook_events WHERE order_id = $1 ORDER BY received_at
        """,
        order_id,
    )
    attempts = await pool.fetch(
        """
        SELECT f.reference, a.action, a.outcome, a.http_status, a.detail, a.created_at
        FROM fulfillment_attempts a JOIN fulfillments f ON f.id = a.fulfillment_id
        WHERE a.order_id = $1 ORDER BY a.id
        """,
        order_id,
    )
    refund_rows = await pool.fetch(
        """
        SELECT id, amount_satang, status, reason, actor, created_at
        FROM refunds WHERE order_id = $1 ORDER BY created_at
        """,
        order_id,
    )

    def plain(rows: list[asyncpg.Record]) -> list[dict[str, object]]:
        return [
            {
                key: value.isoformat() if isinstance(value, datetime) else
                str(value) if isinstance(value, UUID) else value
                for key, value in dict(row).items()
            }
            for row in rows
        ]

    payload = {
        "id": order["id"],
        "status": order["status"],
        "email": _email_for(session, order["email"]),
        "game_uid": _uid_for(session, order["game_uid"]),
        "payment_method": order["payment_method"],
        "voucher_code": order["voucher_code"],
        "subtotal_satang": order["subtotal_satang"],
        "discount_satang": order["discount_satang"],
        "net_satang": order["net_satang"],
        "fee_satang": order["fee_satang"],
        "total_satang": order["total_satang"],
        "vat_satang": order["vat_satang"],
        "paid_satang": order["paid_satang"],
        "refunded_satang": order["refunded_satang"],
        "created_at": order["created_at"].isoformat(),
        "items": plain(items),
        "timeline": plain(events),
        "webhooks": plain(webhooks),
        "fulfillment_attempts": plain(attempts),
        "refunds": plain(refund_rows),
    }
    return Response(dumps(payload), media_type=JSON)


@router.post("/orders/{order_id}/refund", status_code=201)
async def refund_order(
    order_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None),
    session: asyncpg.Record = _finance,
) -> Response:
    key = idempotency.require_key(idempotency_key)
    raw_body = await request.body()
    body = RefundRequest.model_validate_json(raw_body)
    actor = _actor(session)

    async def handler(conn: asyncpg.Connection) -> tuple[int, bytes]:
        refund_id = await refunds.request_refund(
            conn, order_id, body.amount_satang, body.reason, actor
        )
        await _audit(
            conn,
            actor,
            "order.refund",
            order_id,
            {"amount_satang": body.amount_satang, "reason": body.reason},
        )
        status = await conn.fetchval("SELECT status FROM orders WHERE id = $1", order_id)
        return 201, dumps(
            {
                "refund_id": str(refund_id),
                "order_id": order_id,
                "amount_satang": body.amount_satang,
                "order_status": status,
            }
        )

    status_code, response_body = await idempotency.run_once(
        request.app.state.db, f"refund:{order_id}", key, raw_body, handler
    )
    media_type = JSON if status_code < 400 else "application/problem+json"
    return Response(response_body, status_code=status_code, media_type=media_type)


@router.post("/orders/{order_id}/retry-fulfillment")
async def retry_fulfillment(
    order_id: str, request: Request, session: asyncpg.Record = _support
) -> Response:
    if not is_valid_order_number(order_id):
        raise Problem(404, "ORDER_NOT_FOUND", "Order not found")
    actor = _actor(session)
    async with request.app.state.db.acquire() as conn, conn.transaction():
        order = await lock_order(conn, order_id)
        if order is None:
            raise Problem(404, "ORDER_NOT_FOUND", "Order not found")
        current = OrderStatus(order["status"])
        if current not in (OrderStatus.FAILED, OrderStatus.NEEDS_REVIEW):
            raise Problem(409, "INVALID_TRANSITION", f"Cannot retry an order in {current}")
        if order["paid_satang"] != order["total_satang"] or order["refunded_satang"] != 0:
            raise Problem(409, "ORDER_NOT_RETRYABLE", "Payment for this order needs review first")
        if not order["holds_inventory"] and not await inventory.reclaim_for_order(
            conn, order_id, order["email"]
        ):
            raise Problem(409, "OUT_OF_STOCK", "The item is no longer available")
        await queue_fulfillment(conn, order_id, current, actor, "Retried by support")
        await _audit(conn, actor, "order.retry_fulfillment", order_id, {"from": current.value})
    return Response(dumps({"order_id": order_id, "status": "IN_QUEUE"}), media_type=JSON)


@router.get("/audit-logs")
async def audit_logs(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    _: asyncpg.Record = _finance,
) -> Response:
    if cursor is not None:
        created_at, log_id = _decode_cursor(cursor)
        if not log_id.isdigit():
            raise Problem(400, "INVALID_CURSOR", "This cursor is not valid")
        rows = await request.app.state.db.fetch(
            """
            SELECT * FROM audit_logs WHERE (created_at, id) < ($1, $2)
            ORDER BY created_at DESC, id DESC LIMIT $3
            """,
            created_at,
            int(log_id),
            limit + 1,
        )
    else:
        rows = await request.app.state.db.fetch(
            "SELECT * FROM audit_logs ORDER BY created_at DESC, id DESC LIMIT $1", limit + 1
        )
    page = rows[:limit]
    next_cursor = (
        _encode_cursor(page[-1]["created_at"], page[-1]["id"]) if len(rows) > limit else None
    )
    items = [
        {
            "id": row["id"],
            "actor": row["actor"],
            "action": row["action"],
            "target": row["target"],
            "detail": json.loads(row["detail"]),
            "created_at": row["created_at"].isoformat(),
        }
        for row in page
    ]
    return Response(dumps({"items": items, "next_cursor": next_cursor}), media_type=JSON)


async def _settlement_text(request: Request) -> tuple[str, str]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise Problem(422, "INVALID_CSV", "Send the settlement CSV in a 'file' field")
        raw = await upload.read()
        name = upload.filename or "settlement.csv"
    else:
        raw = await request.body()
        name = "settlement.csv"
    try:
        return raw.decode("utf-8"), name
    except UnicodeDecodeError as error:
        raise Problem(422, "INVALID_CSV", "The file must be UTF-8") from error


@router.post("/reconciliation", status_code=201)
async def run_reconciliation(request: Request, session: asyncpg.Record = _finance) -> Response:
    text, name = await _settlement_text(request)
    rows = reconciliation.parse_settlement(text)
    async with request.app.state.db.acquire() as conn, conn.transaction():
        result = await reconciliation.run(conn, rows, name[:200], _actor(session))
    return Response(dumps(result), status_code=201, media_type=JSON)


@router.get("/reconciliation")
async def list_reconciliations(request: Request, _: asyncpg.Record = _finance) -> Response:
    rows = await request.app.state.db.fetch(
        "SELECT id, file_name, uploaded_by, row_count, mismatch_count, created_at "
        "FROM reconciliation_runs ORDER BY created_at DESC LIMIT 50"
    )
    items = [
        {**dict(row), "id": str(row["id"]), "created_at": row["created_at"].isoformat()}
        for row in rows
    ]
    return Response(dumps({"items": items}), media_type=JSON)


@router.get("/dashboard")
async def dashboard(request: Request, _: asyncpg.Record = _viewer) -> Response:
    row = await request.app.state.db.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM orders WHERE created_at > now() - interval '1 minute')
              AS orders_last_minute,
          (SELECT COALESCE(sum(CASE kind WHEN 'payment' THEN amount_satang
                                         ELSE -amount_satang END), 0)
           FROM ledger_entries
           WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Bangkok')
                               AT TIME ZONE 'Asia/Bangkok') AS sales_today_satang,
          (SELECT count(*) FROM jobs WHERE status IN ('queued','running')) AS queue_depth,
          (SELECT count(*) FROM jobs WHERE status = 'dead') AS dead_letter
        """
    )
    return Response(dumps({key: int(value) for key, value in dict(row).items()}), media_type=JSON)


@router.get("/products")
async def admin_products(request: Request, _: asyncpg.Record = _superadmin) -> Response:
    rows = await request.app.state.db.fetch("SELECT * FROM products ORDER BY price_satang")
    keys = ("sku", "slug", "name", "price_satang", "stock_remaining", "is_active")
    return Response(
        dumps({"items": [{key: row[key] for key in keys} for row in rows]}), media_type=JSON
    )


@router.patch("/products/{sku}")
async def patch_product(
    sku: str, request: Request, session: asyncpg.Record = _superadmin
) -> Response:
    body = ProductPatch.model_validate_json(await request.body())
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise Problem(422, "NOTHING_TO_UPDATE", "Provide at least one field")
    assignments = ", ".join(f"{column} = ${i}" for i, column in enumerate(changes, start=2))
    async with request.app.state.db.acquire() as conn, conn.transaction():
        updated = await conn.fetchval(
            f"UPDATE products SET {assignments}, updated_at = now() WHERE sku = $1 RETURNING sku",
            sku,
            *changes.values(),
        )
        if updated is None:
            raise Problem(404, "PRODUCT_NOT_FOUND", "Product not found")
        await _audit(conn, _actor(session), "product.update", sku, changes)
    return Response(dumps({"sku": sku, **changes}), media_type=JSON)


@router.get("/vouchers")
async def admin_vouchers(request: Request, _: asyncpg.Record = _superadmin) -> Response:
    rows = await request.app.state.db.fetch("SELECT * FROM vouchers ORDER BY code")
    keys = ("code", "kind", "used_count", "max_total_uses", "is_active")
    return Response(
        dumps({"items": [{key: row[key] for key in keys} for row in rows]}), media_type=JSON
    )


@router.patch("/vouchers/{code}")
async def patch_voucher(
    code: str, body: VoucherPatch, request: Request, session: asyncpg.Record = _superadmin
) -> Response:
    async with request.app.state.db.acquire() as conn, conn.transaction():
        updated = await conn.fetchval(
            "UPDATE vouchers SET is_active = $2 WHERE code = $1 RETURNING code",
            code,
            body.is_active,
        )
        if updated is None:
            raise Problem(404, "NOT_FOUND", "Voucher not found")
        await _audit(conn, _actor(session), "voucher.update", code, {"is_active": body.is_active})
    return Response(dumps({"code": code, "is_active": body.is_active}), media_type=JSON)


@router.get("/admin-users")
async def admin_users(request: Request, _: asyncpg.Record = _superadmin) -> Response:
    rows = await request.app.state.db.fetch(
        "SELECT email, role, is_active FROM admin_users ORDER BY email"
    )
    return Response(dumps({"items": [dict(row) for row in rows]}), media_type=JSON)


@router.post("/admin-users", status_code=201)
async def create_admin_user(
    body: AdminCreate, request: Request, session: asyncpg.Record = _superadmin
) -> Response:
    password_hash = auth.hash_password(body.password)
    async with request.app.state.db.acquire() as conn, conn.transaction():
        created = await conn.fetchval(
            """
            INSERT INTO admin_users (email, password_hash, role) VALUES ($1, $2, $3)
            ON CONFLICT (email) DO NOTHING RETURNING email
            """,
            body.email,
            password_hash,
            body.role,
        )
        if created is None:
            raise Problem(409, "ADMIN_EXISTS", "This email is already an admin")
        await _audit(conn, _actor(session), "admin.create", body.email, {"role": body.role})
    payload = dumps({"email": body.email, "role": body.role})
    return Response(payload, status_code=201, media_type=JSON)
