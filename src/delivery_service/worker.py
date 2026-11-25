"""Idempotent parcel consumer. ACK follows a durable result or confirmed retry."""

import asyncio
import logging
import time
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
from aio_pika.abc import AbstractIncomingMessage
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from delivery_service.broker import DELAYS, DLQ, QUEUE, Broker
from delivery_service.db import Outbox, Parcel, database, utcnow
from delivery_service.domain import Problem, delivery_cost, reporting_day
from delivery_service.rates import Rates
from delivery_service.schemas import ParcelData, ParcelMessage
from delivery_service.settings import Settings

logger = logging.getLogger(__name__)
RECOVERY_INTERVAL = 60
# Give a newly published message its full sequence of delayed retries first.
PENDING_TIMEOUT = timedelta(seconds=sum(DELAYS), minutes=5)


async def calculate(
    message: ParcelMessage, sessions: async_sessionmaker[AsyncSession], rates: Rates
) -> None:
    """Persist pending registration, then atomically commit calculation and outbox."""
    started = time.monotonic()
    async with sessions.begin() as session:
        await session.execute(
            insert(Parcel)
            .values(
                id=message.id,
                owner_id=message.owner_id,
                created_at=message.created_at,
                status="pending",
                recover_after=utcnow() + PENDING_TIMEOUT,
                **message.parcel.model_dump(),
            )
            .on_conflict_do_nothing(index_elements=[Parcel.id])
        )
        parcel = await session.get(Parcel, message.id)
        if parcel is None or parcel.owner_id != message.owner_id:
            raise ValueError("Conflicting message owner")
        if parcel.status == "calculated":
            return
    rate, rate_date = await rates.get()
    async with sessions.begin() as session:
        parcel = (
            await session.execute(select(Parcel).where(Parcel.id == message.id).with_for_update())
        ).scalar_one()
        if parcel.status == "calculated":
            return
        cost = delivery_cost(parcel.weight_kg, parcel.declared_value_usd, rate)
        cents = int(cost * 100)
        if cents > 9223372036854775807:
            raise ValueError("Delivery cost exceeds reporting integer range")
        now = utcnow()
        parcel.delivery_cost_rub = cost
        parcel.exchange_rate, parcel.rate_date = rate, rate_date
        parcel.calculated_at, parcel.status = now, "calculated"
        parcel.recover_after = None
        event_id = uuid4()
        session.add(
            Outbox(
                id=event_id,
                parcel_id=parcel.id,
                payload={
                    "event_id": str(event_id),
                    "parcel_id": str(parcel.id),
                    "type_id": parcel.type_id,
                    "amount_kopecks": cents,
                    "weight_kg": str(parcel.weight_kg),
                    "declared_value_usd": str(parcel.declared_value_usd),
                    "rate": str(rate),
                    "rate_date": rate_date,
                    "calculated_at": now.isoformat(),
                    "day": reporting_day(now),
                    "formula_version": 1,
                },
            )
        )

    logger.info(
        "calculation_completed",
        extra={
            "parcel_id": str(message.id),
            "event_id": str(event_id),
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )


async def consume(
    incoming: AbstractIncomingMessage,
    sessions: async_sessionmaker[AsyncSession],
    rates: Rates,
    broker: Broker,
) -> None:
    async with incoming.process(requeue=True):
        try:
            message = ParcelMessage.model_validate_json(incoming.body)
        except ValidationError:
            await broker.publish(incoming.body, incoming.message_id or "invalid", queue=DLQ)
            logger.error("invalid_message")
            return
        try:
            await calculate(message, sessions, rates)
        except Exception as exc:
            attempt = int(str(incoming.headers.get("attempt", 0)))
            logger.warning(
                "calculation_retry", extra={"parcel_id": str(message.id), "attempt": attempt}
            )
            if attempt < len(DELAYS):
                await broker.publish(
                    incoming.body,
                    str(message.id),
                    queue=f"{QUEUE}.retry.{DELAYS[attempt]}",
                    attempt=attempt + 1,
                )
            else:
                waiting_for_rate = isinstance(exc, RedisError) or (
                    isinstance(exc, Problem)
                    and exc.code in {"rate_unavailable", "rate_refresh_busy"}
                )
                async with sessions.begin() as session:
                    await session.execute(
                        update(Parcel)
                        .where(
                            Parcel.id == message.id,
                            Parcel.owner_id == message.owner_id,
                            Parcel.status != "calculated",
                        )
                        .values(
                            status="pending" if waiting_for_rate else "failed",
                            recover_after=utcnow() if waiting_for_rate else None,
                        )
                    )
                if not waiting_for_rate:
                    await broker.publish(incoming.body, str(message.id), queue=DLQ, attempt=attempt)


def parcel_message(parcel: Parcel) -> ParcelMessage:
    return ParcelMessage(
        id=parcel.id,
        owner_id=parcel.owner_id,
        created_at=parcel.created_at,
        parcel=ParcelData.model_validate(parcel, from_attributes=True),
    )


async def recover_due(
    sessions: async_sessionmaker[AsyncSession], rates: Rates, broker: Broker
) -> None:
    """Requeue a bounded batch, holding one row lock until publication is confirmed."""
    for index in range(50):
        async with sessions.begin() as session:
            parcel = await session.scalar(
                select(Parcel)
                .where(Parcel.status.in_(("failed", "pending")), Parcel.recover_after <= utcnow())
                .order_by(Parcel.recover_after, Parcel.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if parcel is None:
                return
            if index == 0:
                await rates.get()
            try:
                message = parcel_message(parcel)
            except ValidationError:
                logger.exception("invalid_stored_parcel", extra={"parcel_id": str(parcel.id)})
                parcel.status, parcel.recover_after = "failed", None
                continue
            await broker.publish(message.model_dump_json().encode(), str(parcel.id))
            parcel.status = "pending"
            parcel.recover_after = utcnow() + PENDING_TIMEOUT


async def retry_parcel(parcel_id: UUID, settings: Settings) -> None:
    engine, sessions = database(settings.database_url)
    broker = Broker(settings)
    try:
        async with sessions() as session:
            parcel = await session.get(Parcel, parcel_id)
            if parcel is None:
                raise ValueError("Parcel not found")
            message = parcel_message(parcel)
            await broker.publish(message.model_dump_json().encode(), str(message.id))
    finally:
        await broker.close()
        await engine.dispose()


async def run(settings: Settings) -> None:
    engine, sessions = database(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    broker = Broker(settings)
    try:
        async with httpx.AsyncClient() as http:
            rates = Rates(redis, http, settings)
            await broker.connect()
            if broker.queue is None:
                raise RuntimeError("Queue unavailable")

            async def handler(incoming: AbstractIncomingMessage) -> None:
                await consume(incoming, sessions, rates, broker)

            await broker.queue.consume(handler)
            while True:
                try:
                    await recover_due(sessions, rates, broker)
                except Exception:
                    logger.exception("parcel_recovery_unavailable")
                await asyncio.sleep(RECOVERY_INTERVAL)
    finally:
        await broker.close()
        await redis.aclose()
        await engine.dispose()
