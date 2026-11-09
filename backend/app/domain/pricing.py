"""Order pricing. Every amount is an integer number of satang.

The order of operations is fixed by the spec (section 5.2):
subtotal -> discount -> net -> fee (on net) -> total -> vat (informational, already inside total).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainError

BASIS_POINTS = 10_000
TRUEMONEY_FEE_BP = 150
CARD_FEE_BP = 290
CARD_FEE_FIXED_SATANG = 300


class PaymentMethod(StrEnum):
    PROMPTPAY = "promptpay"
    TRUEMONEY = "truemoney"
    CARD = "card"


class VoucherKind(StrEnum):
    PERCENT = "percent"
    FLAT = "flat"


@dataclass(frozen=True)
class Line:
    unit_price_satang: int
    qty: int


@dataclass(frozen=True)
class VoucherTerms:
    code: str
    kind: VoucherKind
    min_subtotal_satang: int
    percent_bp: int = 0
    max_discount_satang: int | None = None
    flat_satang: int = 0


@dataclass(frozen=True)
class PriceBreakdown:
    subtotal_satang: int
    discount_satang: int
    net_satang: int
    fee_satang: int
    total_satang: int
    vat_satang: int


def _require_int(value: object, name: str) -> None:
    # bool is a subclass of int, so it has to be rejected explicitly.
    if type(value) is not int:
        raise TypeError(f"{name} must be int satang, got {type(value).__name__}")


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _round_half_up_div(numerator: int, denominator: int) -> int:
    return (2 * numerator + denominator) // (2 * denominator)


def compute_subtotal(lines: Sequence[Line]) -> int:
    if not lines:
        raise DomainError("EMPTY_ORDER", "An order needs at least one item")
    subtotal = 0
    for line in lines:
        _require_int(line.unit_price_satang, "unit_price_satang")
        _require_int(line.qty, "qty")
        if line.unit_price_satang < 0 or line.qty <= 0:
            raise DomainError("INVALID_LINE", "Price must be >= 0 and quantity must be > 0")
        subtotal += line.unit_price_satang * line.qty
    return subtotal


def compute_discount(subtotal_satang: int, voucher: VoucherTerms | None) -> int:
    if voucher is None:
        return 0
    if subtotal_satang < voucher.min_subtotal_satang:
        raise DomainError("VOUCHER_MIN_SPEND", "Order does not reach the voucher minimum spend")
    if voucher.kind is VoucherKind.PERCENT:
        discount = subtotal_satang * voucher.percent_bp // BASIS_POINTS
        if voucher.max_discount_satang is not None:
            discount = min(discount, voucher.max_discount_satang)
    else:
        discount = voucher.flat_satang
    return min(discount, subtotal_satang)


def compute_fee(net_satang: int, method: PaymentMethod) -> int:
    if method is PaymentMethod.PROMPTPAY:
        return 0
    if method is PaymentMethod.TRUEMONEY:
        return _ceil_div(net_satang * TRUEMONEY_FEE_BP, BASIS_POINTS)
    return _ceil_div(net_satang * CARD_FEE_BP, BASIS_POINTS) + CARD_FEE_FIXED_SATANG


def compute_vat(total_satang: int) -> int:
    return total_satang - _round_half_up_div(total_satang * 100, 107)


def price_order(
    lines: Sequence[Line], voucher: VoucherTerms | None, method: PaymentMethod
) -> PriceBreakdown:
    subtotal = compute_subtotal(lines)
    discount = compute_discount(subtotal, voucher)
    net = subtotal - discount
    fee = compute_fee(net, method)
    total = net + fee
    return PriceBreakdown(
        subtotal_satang=subtotal,
        discount_satang=discount,
        net_satang=net,
        fee_satang=fee,
        total_satang=total,
        vat_satang=compute_vat(total),
    )
