import pytest

from app.domain.errors import DomainError
from app.domain.masking import mask_email, mask_game_uid
from app.services.reconciliation import SettlementRow, compare, parse_settlement

HEADER = "event_id,order_id,type,amount_satang,settled_at\n"


def test_mask_email_hides_the_local_part():
    assert mask_email("somchai@example.com") == "s***@example.com"


@pytest.mark.parametrize(("uid", "expected"), [("123456789", "12***89"), ("1234", "****")])
def test_mask_game_uid(uid, expected):
    assert mask_game_uid(uid) == expected


def test_parse_accepts_a_byte_order_mark():
    rows = parse_settlement("﻿" + HEADER + "e1,o1,payment.succeeded,100,2026-01-01\n")
    assert rows == [SettlementRow("e1", "o1", "payment.succeeded", 100, "2026-01-01")]


@pytest.mark.parametrize(
    "text",
    [
        "wrong,header\n",
        HEADER + "e1,o1,payment.succeeded,10.50,2026\n",
        HEADER + "e1,o1,payment.succeeded,-1,2026\n",
        HEADER + "too,few\n",
    ],
)
def test_parse_rejects_bad_files(text):
    with pytest.raises(DomainError) as caught:
        parse_settlement(text)
    assert caught.value.code == "INVALID_CSV"


def _entry(amount=100, kind="payment", status="COMPLETED", total=100, order="o1"):
    return {
        "order_id": order,
        "kind": kind,
        "amount_satang": amount,
        "status": status,
        "total_satang": total,
    }


def _row(event="e1", kind="payment.succeeded", amount=100, order="o1"):
    return SettlementRow(event, order, kind, amount, "2026-01-01")


def _categories(rows, ledger, pending=()):
    return [m.category for m in compare(rows, ledger, list(pending))]


def test_matching_file_has_no_mismatches():
    assert _categories([_row()], {"e1": _entry()}) == []


def test_each_mismatch_category():
    assert _categories([_row()], {}) == ["missing_in_system"]
    assert _categories([], {"e1": _entry()}) == ["missing_at_provider"]
    assert _categories([_row(amount=99)], {"e1": _entry()}) == ["amount_mismatch"]
    assert _categories([_row(kind="payment.refunded")], {"e1": _entry()}) == ["status_mismatch"]


def test_payment_on_an_unpaid_order_is_a_status_mismatch():
    assert _categories([_row()], {"e1": _entry(status="EXPIRED")}) == ["status_mismatch"]


def test_payment_that_differs_from_the_order_total_is_flagged():
    assert _categories([_row(amount=90)], {"e1": _entry(amount=90)}) == ["amount_mismatch"]


def test_pending_refund_is_reported():
    assert _categories([], {}, [{"order_id": "o1", "amount_satang": 5}]) == ["missing_at_provider"]


def test_duplicate_rows_are_reported():
    assert _categories([_row(), _row()], {"e1": _entry()}) == ["status_mismatch"]
