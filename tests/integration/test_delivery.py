import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from delivery_service.broker import DLQ, Broker
from delivery_service.db import Outbox, Parcel, utcnow
from delivery_service.domain import Problem
from delivery_service.reporting import Reports
from delivery_service.schemas import ParcelInput, ParcelMessage
from delivery_service.services import Parcels
from delivery_service.worker import calculate, consume

pytestmark = pytest.mark.integration


def message() -> ParcelMessage:
    return ParcelMessage(
        id=uuid4(),
        owner_id=uuid4(),
        created_at=utcnow(),
        parcel=ParcelInput(
            name="Integration", weight_kg=Decimal("2"), declared_value_usd=Decimal("100"), type_id=2
        ),
    )


async def test_100_companies_exactly_one_winner(db: Any, redis: Any, settings: Any) -> None:
    data = message()
    rates = AsyncMock()
    rates.get.return_value = (Decimal("90"), "2026-01-01")
    await calculate(data, db, rates)
    parcels = Parcels(db, redis, AsyncMock(), settings.session_secret.get_secret_value())
    token = await parcels.assignment_token(data.owner_id, data.id)

    async def assign(company: int) -> int | None:
        try:
            return await parcels.assign(data.id, token, company)
        except Problem as exc:
            assert exc.status == 409
            return None

    results = await asyncio.gather(*(assign(i) for i in range(1, 101)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert await parcels.assign(data.id, token, winners[0]) == winners[0]


async def test_parallel_duplicate_calculations(db: Any) -> None:
    data = message()
    rates = AsyncMock()
    rates.get.return_value = (Decimal("90"), "2026-01-01")
    await asyncio.gather(*(calculate(data, db, rates) for _ in range(10)))
    async with db() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Outbox).where(Outbox.parcel_id == data.id)
            )
            == 1
        )
        assert (await session.get(Parcel, data.id)).delivery_cost_rub == Decimal("180.00")


async def test_broker_redelivery_and_mongo_exactly_once(db: Any, settings: Any) -> None:
    data = message()
    broker = Broker(settings)
    reports = Reports(settings.mongo_url.get_secret_value())
    rates = AsyncMock()
    rates.get.return_value = (Decimal("90"), "2026-01-01")
    try:
        await broker.connect()
        assert broker.queue is not None
        await broker.publish(data.model_dump_json().encode(), str(data.id))
        first = await broker.queue.get(timeout=10)
        assert first is not None
        # Simulate a crash after PostgreSQL commit but before acknowledgement.
        await calculate(ParcelMessage.model_validate_json(first.body), db, rates)
        await first.nack(requeue=True)
        redelivered = await broker.queue.get(timeout=10)
        assert redelivered is not None
        await consume(redelivered, db, rates, broker)
        async with db() as session:
            event = (await session.scalars(select(Outbox).where(Outbox.parcel_id == data.id))).one()
        await reports.initialize()
        before = await reports.daily(event.payload["day"])
        await reports.record(event.payload)
        await reports.record(event.payload)  # crash before outbox delivered_at commit
        after = await reports.daily(event.payload["day"])
        previous = sum(row["count"] for row in before)
        assert sum(row["count"] for row in after) == previous + 1
        assert after == await reports.daily(event.payload["day"], raw=True)
    finally:
        await broker.close()
        await reports.close()


async def test_rejected_registration_reaches_failed_queue(settings: Any) -> None:
    broker = Broker(settings)
    data = message()
    try:
        await broker.publish(data.model_dump_json().encode(), str(data.id))
        assert broker.queue is not None and broker.channel is not None
        incoming = await broker.queue.get(timeout=5)
        assert incoming is not None and incoming.message_id == str(data.id)
        await incoming.reject(requeue=False)
        failed = await broker.channel.get_queue(DLQ)
        async with asyncio.timeout(15), failed.iterator() as messages:
            rejected = await anext(messages)
            assert rejected.message_id == str(data.id)
            assert rejected.body == incoming.body
            await rejected.ack()
    finally:
        await broker.close()


async def test_daily_report_groups_types_and_isolates_days(settings: Any) -> None:
    reports = Reports(settings.mongo_url.get_secret_value())
    day, next_day = "2025-01-01", "2025-01-02"
    try:
        await reports.initialize()
        for event_day, type_id, amount in [
            (day, 1, 12001),
            (day, 1, 799),
            (day, 2, 540),
            (day, 3, 99),
            (next_day, 1, 300),
        ]:
            await reports.record(
                {
                    "event_id": str(uuid4()),
                    "calculated_at": f"{event_day}T12:00:00+00:00",
                    "day": event_day,
                    "type_id": type_id,
                    "amount_kopecks": amount,
                }
            )
        totals = await reports.daily(day)
        assert totals == [
            {"_id": 1, "amount_kopecks": 12800, "count": 2},
            {"_id": 2, "amount_kopecks": 540, "count": 1},
            {"_id": 3, "amount_kopecks": 99, "count": 1},
        ]
        assert totals == await reports.daily(day, raw=True)
    finally:
        await reports.close()
