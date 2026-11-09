import ast
from pathlib import Path

import pytest

from app.domain.errors import DomainError
from app.domain.pricing import (
    Line,
    PaymentMethod,
    PriceBreakdown,
    VoucherKind,
    VoucherTerms,
    compute_fee,
    compute_vat,
    price_order,
)

SAVE10 = VoucherTerms(
    code="SAVE10",
    kind=VoucherKind.PERCENT,
    percent_bp=1000,
    max_discount_satang=5000,
    min_subtotal_satang=20000,
)
FLAT20 = VoucherTerms(
    code="FLAT20", kind=VoucherKind.FLAT, flat_satang=2000, min_subtotal_satang=10000
)


@pytest.mark.parametrize(
    ("lines", "voucher", "method", "expected"),
    [
        pytest.param(
            [Line(34900, 2)],
            SAVE10,
            PaymentMethod.CARD,
            PriceBreakdown(69800, 5000, 64800, 2180, 66980, 4382),
            id="spec-1-diamond1000x2-save10-card",
        ),
        pytest.param(
            [Line(17900, 1), Line(9900, 1)],
            FLAT20,
            PaymentMethod.TRUEMONEY,
            PriceBreakdown(27800, 2000, 25800, 387, 26187, 1713),
            id="spec-2-pass-skin-flat20-truemoney",
        ),
        pytest.param(
            [Line(159000, 1)],
            SAVE10,
            PaymentMethod.PROMPTPAY,
            PriceBreakdown(159000, 5000, 154000, 0, 154000, 10075),
            id="spec-3-diamond5000-save10-promptpay",
        ),
    ],
)
def test_spec_examples_match_to_the_satang(lines, voucher, method, expected):
    assert price_order(lines, voucher, method) == expected


def test_percent_discount_rounds_down_before_the_cap():
    voucher = VoucherTerms("P", VoucherKind.PERCENT, min_subtotal_satang=0, percent_bp=1000)
    assert price_order([Line(20009, 1)], voucher, PaymentMethod.PROMPTPAY).discount_satang == 2000


def test_percent_discount_is_limited_by_the_cap():
    assert price_order([Line(100000, 1)], SAVE10, PaymentMethod.PROMPTPAY).discount_satang == 5000


def test_minimum_spend_is_inclusive():
    assert price_order([Line(20000, 1)], SAVE10, PaymentMethod.PROMPTPAY).discount_satang == 2000


def test_below_minimum_spend_is_rejected():
    with pytest.raises(DomainError) as caught:
        price_order([Line(19999, 1)], SAVE10, PaymentMethod.PROMPTPAY)
    assert caught.value.code == "VOUCHER_MIN_SPEND"


def test_minimum_spend_uses_subtotal_not_net():
    # 20,000 subtotal passes even though the discount takes net below 20,000.
    result = price_order([Line(20000, 1)], SAVE10, PaymentMethod.PROMPTPAY)
    assert result.net_satang == 18000


def test_flat_discount_never_exceeds_subtotal():
    voucher = VoucherTerms("F", VoucherKind.FLAT, min_subtotal_satang=0, flat_satang=5000)
    result = price_order([Line(3000, 1)], voucher, PaymentMethod.PROMPTPAY)
    assert (result.discount_satang, result.total_satang) == (3000, 0)


@pytest.mark.parametrize(
    ("net", "expected"),
    [(0, 0), (1, 1), (66, 1), (67, 2), (20000, 300), (25800, 387)],
)
def test_truemoney_fee_rounds_up(net, expected):
    assert compute_fee(net, PaymentMethod.TRUEMONEY) == expected


@pytest.mark.parametrize(
    ("net", "expected"), [(0, 300), (1, 301), (10000, 590), (64800, 2180)]
)
def test_card_fee_rounds_up_then_adds_fixed_fee(net, expected):
    assert compute_fee(net, PaymentMethod.CARD) == expected


@pytest.mark.parametrize(
    ("total", "expected"), [(0, 0), (107, 7), (66980, 4382), (26187, 1713), (154000, 10075)]
)
def test_vat(total, expected):
    assert compute_vat(total) == expected


def test_vat_rounding_boundary():
    # 107 is odd, so an exact .5 cannot occur. These are the closest values on each side.
    assert compute_vat(23) == 23 - 21  # 2300 / 107 = 21.495...
    assert compute_vat(84) == 84 - 79  # 8400 / 107 = 78.504...


def test_float_price_is_rejected():
    with pytest.raises(TypeError):
        price_order([Line(349.0, 1)], None, PaymentMethod.PROMPTPAY)  # type: ignore[arg-type]


def test_bool_quantity_is_rejected():
    with pytest.raises(TypeError):
        price_order([Line(34900, True)], None, PaymentMethod.PROMPTPAY)


@pytest.mark.parametrize("lines", [[], [Line(100, 0)], [Line(100, -1)], [Line(-1, 1)]])
def test_invalid_lines_are_rejected(lines):
    with pytest.raises(DomainError):
        price_order(lines, None, PaymentMethod.PROMPTPAY)


def test_domain_layer_contains_no_float_arithmetic():
    domain_dir = Path(__file__).resolve().parents[2] / "app" / "domain"
    offenders: list[str] = []
    for path in domain_dir.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            is_literal = isinstance(node, ast.Constant) and isinstance(node.value, float)
            is_division = isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
            is_call = isinstance(node, ast.Call) and getattr(node.func, "id", "") == "float"
            if is_literal or is_division or is_call:
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
