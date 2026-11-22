from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from delivery_service.broker import DELAYS, DLQ
from delivery_service.db import Outbox, Parcel, utcnow
from delivery_service.domain import Problem
from delivery_service.rates import Rates
from delivery_service.schemas import ParcelInput, ParcelMessage
from delivery_service.worker import calculate, consume


async def test_rate_cache_and_nominal(redis: Any, settings: Any) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json={"Date": "2026-01-01", "Valute": {"USD": {"Value": 180, "Nominal": 2}}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rates = Rates(redis, http, settings)
        assert await rates.get() == (Decimal("90"), "2026-01-01")
        assert await rates.get() == (Decimal("90"), "2026-01-01")
        assert len(calls) == 1
        assert await redis.ttl("exchange:usd") > 0


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"Date": "x", "Valute": {"USD": {"Value": -1, "Nominal": 1}}}),
    ],
)
async def test_no_stale_rate(redis: Any, settings: Any, response: httpx.Response) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        with pytest.raises(Problem):
            await Rates(redis, http, settings).get()
        assert await redis.get("exchange:lock") is None
        assert await redis.get("exchange:usd") is None


def message() -> ParcelMessage:
    return ParcelMessage(
        id=uuid4(),
        owner_id=uuid4(),
        created_at=utcnow(),
        parcel=ParcelInput(
            name="Laptop", weight_kg=Decimal("2"), type_id=2, declared_value_usd=Decimal("100")
        ),
    )


async def test_calculate_idempotent_and_outbox(db: Any) -> None:
    data = message()
    rates = AsyncMock()
    rates.get.return_value = (Decimal("90"), "2026-01-01")
    await calculate(data, db, rates)
    await calculate(data, db, rates)
    async with db() as session:
        parcel = await session.get(Parcel, data.id)
        assert parcel.delivery_cost_rub == Decimal("180.00")
        assert parcel.status == "calculated"
        assert await session.scalar(select(func.count()).select_from(Outbox)) == 1
    rates.get.assert_awaited_once()


async def test_calculation_outage_keeps_pending(db: Any) -> None:
    data = message()
    rates = AsyncMock()
    rates.get.side_effect = Problem(503, "rate", "offline")
    with pytest.raises(Problem):
        await calculate(data, db, rates)
    async with db() as session:
        parcel = await session.get(Parcel, data.id)
        assert parcel.status == "pending" and parcel.delivery_cost_rub is None
        assert await session.scalar(select(func.count()).select_from(Outbox)) == 0


class Incoming:
    def __init__(self, body: bytes, attempt: int = 0) -> None:
        self.body, self.headers, self.message_id = body, {"attempt": attempt}, "test"
        self.acked = False

    def process(self, requeue: bool) -> Any:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def context() -> Any:
            yield
            self.acked = True

        return context()


async def test_retry_confirm_before_ack(db: Any) -> None:
    data = message()
    incoming = Incoming(data.model_dump_json().encode())
    broker = AsyncMock()
    with patch("delivery_service.worker.calculate", side_effect=OSError("offline")):
        await consume(incoming, db, AsyncMock(), broker)
    assert incoming.acked
    assert broker.publish.call_args.kwargs["attempt"] == 1
    incoming.acked = False
    broker.publish.side_effect = OSError("broker down")
    with (
        patch("delivery_service.worker.calculate", side_effect=OSError("offline")),
        pytest.raises(OSError),
    ):
        await consume(incoming, db, AsyncMock(), broker)
    assert not incoming.acked


async def test_failed_and_invalid_messages(db: Any) -> None:
    data = message()
    rates = AsyncMock()
    rates.get.side_effect = Problem(503, "rate", "offline")
    broker = AsyncMock()
    await consume(Incoming(data.model_dump_json().encode(), len(DELAYS)), db, rates, broker)
    assert broker.publish.call_args.kwargs["queue"] == DLQ
    async with db() as session:
        assert (await session.get(Parcel, data.id)).status == "failed"
    await consume(Incoming(b"bad json"), db, rates, broker)
    assert broker.publish.call_args.kwargs["queue"] == DLQ
