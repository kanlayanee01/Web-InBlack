import itertools

import pytest

from app.domain.errors import DomainError
from app.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    BADGE_BY_STATUS,
    OrderStatus,
    assert_transition,
    can_transition,
)

# Written out by hand on purpose: this is an independent copy, not derived from the code under test.
EXPECTED_ALLOWED = {
    ("PENDING_PAYMENT", "PAID"),
    ("PENDING_PAYMENT", "EXPIRED"),
    ("PENDING_PAYMENT", "PAYMENT_FAILED"),
    ("PAID", "IN_QUEUE"),
    ("IN_QUEUE", "IN_PROGRESS"),
    ("IN_PROGRESS", "COMPLETED"),
    ("IN_PROGRESS", "FAILED"),
    ("FAILED", "REFUNDED"),
    ("COMPLETED", "REFUNDED"),
    ("EXPIRED", "PAID"),
    ("EXPIRED", "REFUNDED"),
    ("FAILED", "IN_QUEUE"),
    ("NEEDS_REVIEW", "IN_QUEUE"),
    ("NEEDS_REVIEW", "COMPLETED"),
    ("NEEDS_REVIEW", "FAILED"),
    ("NEEDS_REVIEW", "REFUNDED"),
    ("PENDING_PAYMENT", "NEEDS_REVIEW"),
    ("PAID", "NEEDS_REVIEW"),
    ("IN_QUEUE", "NEEDS_REVIEW"),
    ("IN_PROGRESS", "NEEDS_REVIEW"),
    ("COMPLETED", "NEEDS_REVIEW"),
    ("EXPIRED", "NEEDS_REVIEW"),
    ("PAYMENT_FAILED", "NEEDS_REVIEW"),
    ("FAILED", "NEEDS_REVIEW"),
    ("REFUNDED", "NEEDS_REVIEW"),
}

ALL_PAIRS = list(itertools.product(OrderStatus, OrderStatus))


@pytest.mark.parametrize(("current", "target"), ALL_PAIRS, ids=lambda s: s.value)
def test_every_pair_of_statuses(current, target):
    expected = (current.value, target.value) in EXPECTED_ALLOWED
    assert can_transition(current, target) is expected
    if expected:
        assert_transition(current, target)
    else:
        with pytest.raises(DomainError) as caught:
            assert_transition(current, target)
        assert caught.value.code == "INVALID_TRANSITION"


def test_late_payment_failure_cannot_undo_a_paid_order():
    for status in (OrderStatus.PAID, OrderStatus.IN_QUEUE, OrderStatus.COMPLETED):
        assert not can_transition(status, OrderStatus.PAYMENT_FAILED)


def test_allowed_set_has_no_surplus_entries():
    assert {(a.value, b.value) for a, b in ALLOWED_TRANSITIONS} == EXPECTED_ALLOWED


def test_every_status_has_a_customer_badge():
    assert set(BADGE_BY_STATUS) == set(OrderStatus)
