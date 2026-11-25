from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from delivery_service.domain import delivery_cost, reporting_day
from delivery_service.schemas import ParcelInput


@pytest.mark.parametrize(
    ("weight", "value", "rate", "expected"),
    [
        ("2", "100", "90", "180.00"),
        ("0.001", "0", "90", "0.05"),
        ("1", "0", "0.01", "0.01"),
        ("0.1", "10", "99.1234", "14.87"),
    ],
)
def test_cost(weight: str, value: str, rate: str, expected: str) -> None:
    assert delivery_cost(Decimal(weight), Decimal(value), Decimal(rate)) == Decimal(expected)


@pytest.mark.parametrize(("weight", "value", "rate"), [(0, 10, 90), (1, -1, 90), (1, 1, 0)])
def test_invalid_cost(weight: int, value: int, rate: int) -> None:
    with pytest.raises(ValueError):
        delivery_cost(Decimal(weight), Decimal(value), Decimal(rate))


def test_moscow_day_boundary() -> None:
    assert reporting_day(datetime.fromisoformat("2026-01-01T20:59:59+00:00")) == "2026-01-01"
    assert reporting_day(datetime.fromisoformat("2026-01-01T21:00:00+00:00")) == "2026-01-02"
    with pytest.raises(ValueError):
        reporting_day(datetime(2026, 1, 1))


@pytest.mark.parametrize(
    "change",
    [
        {"name": "   "},
        {"name": "x" * 256},
        {"weight_kg": "0"},
        {"weight_kg": "NaN"},
        {"weight_kg": "Infinity"},
        {"weight_kg": "1.0001"},
        {"type_id": 4},
        {"declared_value_usd": "-1"},
        {"declared_value_usd": "1.001"},
        {"owner_id": "spoofed"},
    ],
)
def test_validation(change: dict[str, object]) -> None:
    data = {"name": "Laptop", "weight_kg": "1", "type_id": 2, "declared_value_usd": "50"}
    with pytest.raises(ValidationError):
        ParcelInput.model_validate({**data, **change})
