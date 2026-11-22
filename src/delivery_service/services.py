"""Application operations with explicit ownership and transaction boundaries."""

import json
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from itsdangerous import URLSafeSerializer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from delivery_service.db import Parcel, ParcelType
from delivery_service.domain import Problem
from delivery_service.schemas import (
    ParcelInput,
    ParcelMessage,
    Registration,
)
from delivery_service.security import digest

RESERVE = """
local old = redis.call('get', KEYS[1])
if old then return old end
redis.call('set', KEYS[1], ARGV[1], 'EX', 86400)
redis.call('set', KEYS[2], ARGV[2], 'EX', 86400)
return ARGV[1]
"""


class Publisher(Protocol):
    async def publish(self, body: bytes, message_id: str) -> None: ...


class Parcels:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        broker: Publisher,
        signing_secret: str,
    ) -> None:
        self.sessions, self.redis, self.broker = sessions, redis, broker
        self.cursor = URLSafeSerializer(signing_secret, salt="parcel-cursor")

    async def register(self, owner: UUID, data: ParcelInput, key: str) -> Registration:
        """Reserve one ID per request key and return only after broker confirmation."""
        if not 1 <= len(key) <= 128:
            raise Problem(422, "invalid_idempotency_key", "Нужен Idempotency-Key длиной 1–128")
        async with self.sessions() as session:
            if await session.get(ParcelType, data.type_id) is None:
                raise Problem(422, "unknown_parcel_type", "Неизвестный тип посылки")
        parcel_id = uuid4()
        message = ParcelMessage(
            id=parcel_id, owner_id=owner, parcel=data, created_at=datetime.now(UTC)
        )
        fingerprint = digest(data.model_dump_json())
        candidate = {"hash": fingerprint, "message": message.model_dump(mode="json")}
        reserved = await cast(
            Awaitable[Any],
            self.redis.eval(
                RESERVE,
                2,
                f"registration:key:{owner}:{digest(key)}",
                f"registration:{parcel_id}",
                json.dumps(candidate),
                json.dumps({"owner": str(owner), "status": "publishing"}),
            ),
        )
        record = json.loads(reserved)
        if record["hash"] != fingerprint:
            raise Problem(
                409, "idempotency_conflict", "Этот ключ уже использован с другими данными"
            )
        message = ParcelMessage.model_validate(record["message"])
        try:
            await self.broker.publish(message.model_dump_json().encode(), str(message.id))
        except Exception as exc:
            raise Problem(
                503, "broker_unavailable", "Повторите запрос с тем же Idempotency-Key"
            ) from exc
        await self.redis.set(
            f"registration:{message.id}",
            json.dumps({"owner": str(owner), "status": "queued"}),
            ex=86400,
        )
        return Registration(
            id=message.id, status="queued", status_url=f"/api/v1/registrations/{message.id}"
        )

    async def status(self, owner: UUID, parcel_id: UUID) -> Registration:
        async with self.sessions() as session:
            parcel = await session.get(Parcel, parcel_id)
            if parcel:
                if parcel.owner_id != owner:
                    raise Problem(404, "parcel_not_found", "Посылка не найдена")
                status = parcel.status
            else:
                cached = await self.redis.get(f"registration:{parcel_id}")
                if not cached or json.loads(cached)["owner"] != str(owner):
                    raise Problem(404, "parcel_not_found", "Посылка не найдена")
                status = json.loads(cached)["status"]
        return Registration(
            id=parcel_id, status=status, status_url=f"/api/v1/registrations/{parcel_id}"
        )
