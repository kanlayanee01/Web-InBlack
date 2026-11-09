"""Calls the real API and validates each response against openapi.yaml."""

import pathlib
import uuid

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from tests.api.conftest import order_body, post_order, signed, wait_for_status, webhook_bytes
from tests.api.test_admin import login, refund
from tests.api.test_auth_and_tracking import sign_in, unique_email

SPEC_PATH = pathlib.Path("/srv/openapi.yaml")
SPEC = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
REGISTRY = Registry().with_resource(
    "urn:spec", Resource.from_contents(SPEC, default_specification=DRAFT202012)
)
MISSING_ORDER = "ORD-1000000000000-081"


def _pointer(*parts: str) -> str:
    return "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def check(response: httpx.Response, template: str, method: str = "get") -> None:
    status = str(response.status_code)
    operation = SPEC["paths"][template][method]
    assert status in operation["responses"], f"{method} {template} returned undocumented {status}"
    declared = operation["responses"][status]
    if "$ref" in declared:
        name = declared["$ref"].rsplit("/", 1)[1]
        declared = SPEC["components"]["responses"][name]
    media_type = response.headers["content-type"].split(";")[0]
    assert media_type in declared["content"], f"{media_type} not documented for {template}"
    if media_type in ("application/json", "application/problem+json"):
        pointer = _pointer("paths", template, method, "responses", status, "content", media_type)
        if "$ref" in operation["responses"][status]:
            pointer = _pointer(
                "components", "responses", name, "content", media_type
            )
        validator = Draft202012Validator(
            {"$ref": f"urn:spec#/{pointer}/schema"}, registry=REGISTRY
        )
        errors = [error.message for error in validator.iter_errors(response.json())]
        assert errors == [], f"{method} {template}: {errors}"


def test_the_specification_itself_is_valid():
    assert SPEC["openapi"].startswith("3.1")
    validate(SPEC)


async def test_catalog_and_operations(client):
    check(await client.get("/api/v1/products"), "/api/v1/products")
    check(await client.get("/api/v1/products/diamond-1000"), "/api/v1/products/{slug}")
    check(await client.get("/api/v1/products/nope"), "/api/v1/products/{slug}")
    check(await client.get("/healthz"), "/healthz")
    check(await client.get("/readyz"), "/readyz")


async def test_metrics_exposes_the_required_series(client, db, send_webhook):
    order = (await post_order(client, order_body())).json()
    await send_webhook(order["id"], order["total_satang"])
    await wait_for_status(db, order["id"], "COMPLETED")
    response = await client.get("/metrics")
    assert response.status_code == 200
    for series in (
        "inblack_http_request_duration_seconds_bucket",
        'inblack_orders{status="COMPLETED"}',
        "inblack_queue_depth",
        "inblack_dead_letter_jobs",
        "inblack_job_retries",
    ):
        assert series in response.text, series


async def test_request_id_follows_the_order_from_webhook_to_job(client, db):
    order = (await post_order(client, order_body())).json()
    raw = webhook_bytes(order["id"], order["total_satang"])
    response = await client.post("/api/v1/webhooks/payment", content=raw, headers=signed(raw))
    request_id = response.headers["x-request-id"]
    assert request_id
    assert await db.fetchval(
        "SELECT trace_id FROM jobs WHERE dedupe_key = $1", f"fulfill:{order['id']}"
    ) == request_id


async def test_order_flow_matches_the_contract(client, db, send_webhook):
    email = unique_email()
    quote = await client.post(
        "/api/v1/orders/quote",
        json={"items": [{"sku": "DIAMOND-1000", "qty": 1}], "payment_method": "card"},
    )
    check(quote, "/api/v1/orders/quote", "post")
    created = await post_order(client, order_body(email=email))
    check(created, "/api/v1/orders", "post")
    order = created.json()
    check(
        await client.post("/api/v1/orders", json=order_body()),
        "/api/v1/orders",
        "post",
    )

    track = {"order_id": order["id"], "email": email}
    check(await client.post("/api/v1/orders/track", json=track), "/api/v1/orders/track", "post")
    wrong = {"order_id": order["id"], "email": "no@example.com"}
    check(await client.post("/api/v1/orders/track", json=wrong), "/api/v1/orders/track", "post")

    raw = webhook_bytes(order["id"], order["total_satang"])
    hook = await client.post("/api/v1/webhooks/payment", content=raw, headers=signed(raw))
    check(hook, "/api/v1/webhooks/payment", "post")
    bad = await client.post("/api/v1/webhooks/payment", content=raw)
    check(bad, "/api/v1/webhooks/payment", "post")
    await wait_for_status(db, order["id"], "COMPLETED")

    headers = await sign_in(client, email)
    check(await client.get(f"/api/v1/orders/{order['id']}", headers=headers), "/api/v1/orders/{id}")
    missing = await client.get(f"/api/v1/orders/{MISSING_ORDER}", headers=headers)
    check(missing, "/api/v1/orders/{id}")
    check(await client.get("/api/v1/me/orders", headers=headers), "/api/v1/me/orders")
    check(await client.get("/api/v1/me/orders"), "/api/v1/me/orders")


async def test_admin_flow_matches_the_contract(client, db, send_webhook):
    order = (await post_order(client, order_body())).json()
    await send_webhook(order["id"], order["total_satang"])
    await wait_for_status(db, order["id"], "COMPLETED")
    headers = await login(client, "finance")

    check(await client.get("/api/v1/admin/orders", headers=headers), "/api/v1/admin/orders")
    check(await client.get("/api/v1/admin/orders"), "/api/v1/admin/orders")
    check(
        await client.get(f"/api/v1/admin/orders/{order['id']}", headers=headers),
        "/api/v1/admin/orders/{id}",
    )
    check(
        await refund(client, headers, order["id"], 100),
        "/api/v1/admin/orders/{id}/refund",
        "post",
    )
    check(
        await refund(client, await login(client, "viewer"), order["id"], 100),
        "/api/v1/admin/orders/{id}/refund",
        "post",
    )
    check(await client.get("/api/v1/admin/audit-logs", headers=headers), "/api/v1/admin/audit-logs")
    check(await client.get("/api/v1/admin/dashboard", headers=headers), "/api/v1/admin/dashboard")
    csv = "event_id,order_id,type,amount_satang,settled_at\n"
    check(
        await client.post(
            "/api/v1/admin/reconciliation",
            content=csv,
            headers={**headers, "Content-Type": "text/csv"},
        ),
        "/api/v1/admin/reconciliation",
        "post",
    )
    check(
        await client.post(
            f"/api/v1/admin/orders/{order['id']}/retry-fulfillment", headers=headers
        ),
        "/api/v1/admin/orders/{id}/retry-fulfillment",
        "post",
    )


async def test_auth_matches_the_contract(client):
    email = f"{uuid.uuid4().hex}@example.com"
    check(
        await client.post("/api/v1/auth/otp/request", json={"email": email}),
        "/api/v1/auth/otp/request",
        "post",
    )
    check(
        await client.post("/api/v1/auth/otp/verify", json={"email": email, "code": "000000"}),
        "/api/v1/auth/otp/verify",
        "post",
    )
    check(
        await client.post(
            "/api/v1/admin/auth/login",
            json={"email": "viewer@inblackm.test", "password": "wrong"},
        ),
        "/api/v1/admin/auth/login",
        "post",
    )


@pytest.mark.parametrize("path", ["/api/v1/_test/reset"])
async def test_reset_is_documented(client, path):
    check(await client.post(path), path, "post")
