"""Order state machine.

The database enforces the same table through a trigger (migration 0001), so a bug in application
code still cannot perform an illegal transition. An integration test keeps the two in sync.

Transitions beyond the spec diagram are listed in EXTENSIONS and justified in docs/adr.
"""

from enum import StrEnum

from app.domain.errors import DomainError


class OrderStatus(StrEnum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAID = "PAID"
    IN_QUEUE = "IN_QUEUE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


S = OrderStatus

DIAGRAM: frozenset[tuple[OrderStatus, OrderStatus]] = frozenset(
    {
        (S.PENDING_PAYMENT, S.PAID),
        (S.PENDING_PAYMENT, S.EXPIRED),
        (S.PENDING_PAYMENT, S.PAYMENT_FAILED),
        (S.PAID, S.IN_QUEUE),
        (S.IN_QUEUE, S.IN_PROGRESS),
        (S.IN_PROGRESS, S.COMPLETED),
        (S.IN_PROGRESS, S.FAILED),
        (S.FAILED, S.REFUNDED),
        (S.COMPLETED, S.REFUNDED),
    }
)

# "Any problem the system cannot decide on its own goes to NEEDS_REVIEW" (spec 5.3).
TO_REVIEW: frozenset[tuple[OrderStatus, OrderStatus]] = frozenset(
    (status, S.NEEDS_REVIEW) for status in OrderStatus if status is not S.NEEDS_REVIEW
)

EXTENSIONS: frozenset[tuple[OrderStatus, OrderStatus]] = frozenset(
    {
        # Spec 6: payment arrives after expiry and stock can be reserved again.
        (S.EXPIRED, S.PAID),
        # Spec 6: payment arrives after expiry with no stock left, so it is refunded automatically.
        (S.EXPIRED, S.REFUNDED),
        # Spec 8.1: POST /admin/orders/{id}/retry-fulfillment needs a way back into the queue.
        (S.FAILED, S.IN_QUEUE),
        (S.NEEDS_REVIEW, S.IN_QUEUE),
        # Ways out of manual review once a human has decided.
        (S.NEEDS_REVIEW, S.COMPLETED),
        (S.NEEDS_REVIEW, S.FAILED),
        (S.NEEDS_REVIEW, S.REFUNDED),
    }
)

ALLOWED_TRANSITIONS: frozenset[tuple[OrderStatus, OrderStatus]] = DIAGRAM | TO_REVIEW | EXTENSIONS


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return (current, target) in ALLOWED_TRANSITIONS


def assert_transition(current: OrderStatus, target: OrderStatus) -> None:
    if not can_transition(current, target):
        raise DomainError("INVALID_TRANSITION", f"Cannot move an order from {current} to {target}")


class CustomerBadge(StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"
    IN_QUEUE = "in_queue"
    PROCESSING = "processing"
    COMPLETED = "completed"
    EXPIRED = "expired"
    PAYMENT_FAILED = "payment_failed"
    REFUNDED = "refunded"
    ACTION_NEEDED = "action_needed"


BADGE_BY_STATUS: dict[OrderStatus, CustomerBadge] = {
    S.PENDING_PAYMENT: CustomerBadge.AWAITING_PAYMENT,
    S.PAID: CustomerBadge.IN_QUEUE,
    S.IN_QUEUE: CustomerBadge.IN_QUEUE,
    S.IN_PROGRESS: CustomerBadge.PROCESSING,
    S.COMPLETED: CustomerBadge.COMPLETED,
    S.EXPIRED: CustomerBadge.EXPIRED,
    S.PAYMENT_FAILED: CustomerBadge.PAYMENT_FAILED,
    S.REFUNDED: CustomerBadge.REFUNDED,
    S.FAILED: CustomerBadge.ACTION_NEEDED,
    S.NEEDS_REVIEW: CustomerBadge.ACTION_NEEDED,
}
