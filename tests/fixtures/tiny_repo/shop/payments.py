"""Payments: charging cards and issuing refunds through the gateway."""

from dataclasses import dataclass

MAX_REFUND_DAYS = 30


@dataclass
class Payment:
    order_id: int
    amount_cents: int
    days_since_purchase: int


def validate_amount(amount_cents: int) -> None:
    """Reject zero, negative or absurdly large amounts."""
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    if amount_cents > 10_000_00:
        raise ValueError("amount exceeds the 10,000 limit")


class PaymentService:
    """Talks to the payment gateway."""

    def __init__(self, gateway):
        self.gateway = gateway

    def charge(self, order_id: int, amount_cents: int) -> str:
        validate_amount(amount_cents)
        return self.gateway.charge(order_id=order_id, amount=amount_cents)

    def process_refund(self, payment: Payment) -> str:
        """Refund a payment. Refunds are only allowed within MAX_REFUND_DAYS of purchase."""
        if payment.days_since_purchase > MAX_REFUND_DAYS:
            raise PermissionError("refund window has expired")
        validate_amount(payment.amount_cents)
        return self.gateway.refund(order_id=payment.order_id, amount=payment.amount_cents)
