"""Pure delivery rules and transport-independent errors."""

from decimal import ROUND_HALF_UP, Decimal


class Problem(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status, self.code, self.detail = status, code, detail
        super().__init__(detail)


def delivery_cost(weight: Decimal, value: Decimal, rate: Decimal) -> Decimal:
    """Calculate RUB, rounding once to kopecks using commercial rounding."""
    if weight <= 0 or value < 0 or rate <= 0:
        raise ValueError("Invalid calculation inputs")
    return ((weight * Decimal("0.5") + value * Decimal("0.01")) * rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
