"""One error format for the whole API: RFC 9457 Problem Details plus a machine-readable `code`."""

import json

from app.domain.errors import DomainError

PROBLEM_CONTENT_TYPE = "application/problem+json"

STATUS_BY_CODE = {
    "OUT_OF_STOCK": 409,
    "VOUCHER_EXHAUSTED": 409,
    "VOUCHER_ALREADY_USED": 409,
    "PER_CUSTOMER_LIMIT": 409,
    "INVALID_TRANSITION": 409,
    "REFUND_EXCEEDS_PAID": 409,
    "ORDER_NOT_REFUNDABLE": 409,
    "PRODUCT_NOT_FOUND": 404,
    "ORDER_NOT_FOUND": 404,
}


class Problem(Exception):
    def __init__(
        self, status: int, code: str, detail: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers or {}


def from_domain(error: DomainError) -> Problem:
    return Problem(STATUS_BY_CODE.get(error.code, 422), error.code, error.detail)


def dumps(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def problem_body(status: int, code: str, detail: str, **extra: object) -> bytes:
    # No request id or timestamp in here: idempotent replays must be byte-identical.
    slug = code.lower().replace("_", "-")
    return dumps(
        {
            "type": f"https://errors.inblackm.test/{slug}",
            "title": code.replace("_", " ").capitalize(),
            "status": status,
            "code": code,
            "detail": detail,
            **extra,
        }
    )
