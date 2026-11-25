"""Application operations with explicit ownership and transaction boundaries."""

import json
import secrets
from collections.abc import Awaitable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from delivery_service.db import Parcel, ParcelType
from delivery_service.domain import Problem
from delivery_service.schemas import (
    ParcelInput,
    ParcelMessage,
    ParcelPage,
    ParcelTypeView,
    ParcelView,
    Registration,
)
from delivery_service.security import digest

PARCEL_TYPES_KEY = "catalog:parcel-types:v1"
PARCEL_TYPES_TTL = 3600
PARCEL_TYPES = TypeAdapter(list[ParcelTypeView])

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

    async def _load_types(self) -> list[ParcelTypeView]:
        async with self.sessions() as session:
            rows = await session.scalars(select(ParcelType).order_by(ParcelType.id))
            return [ParcelTypeView(id=row.id, name=row.name) for row in rows]

    async def list_types(self) -> list[ParcelTypeView]:
        try:
            cached = await self.redis.get(PARCEL_TYPES_KEY)
        except RedisError:
            return await self._load_types()
        if cached is not None:
            with suppress(ValidationError):
                return PARCEL_TYPES.validate_json(cached)
        types = await self._load_types()
        with suppress(RedisError):
            await self.redis.set(
                PARCEL_TYPES_KEY, PARCEL_TYPES.dump_json(types), ex=PARCEL_TYPES_TTL
            )
        return types

    async def register(self, owner: UUID, data: ParcelInput, key: str) -> Registration:
        """Reserve one ID per request key and return only after broker confirmation."""
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

    @staticmethod
    def view(parcel: Parcel, type_name: str) -> ParcelView:
        cost = parcel.delivery_cost_rub
        return ParcelView.model_validate(
            {
                "id": parcel.id,
                "name": parcel.name,
                "weight_kg": parcel.weight_kg,
                "type_id": parcel.type_id,
                "type_name": type_name,
                "declared_value_usd": parcel.declared_value_usd,
                "delivery_cost_rub": cost,
                "delivery_cost_display": "Не рассчитано" if cost is None else f"{cost:.2f} ₽",
                "status": parcel.status,
                "carrier_id": parcel.carrier_id,
                "created_at": parcel.created_at,
            }
        )

    async def get(self, owner: UUID, parcel_id: UUID) -> ParcelView:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(Parcel, ParcelType.name)
                    .join(ParcelType)
                    .where(Parcel.id == parcel_id, Parcel.owner_id == owner)
                )
            ).first()
            if row is None:
                raise Problem(404, "parcel_not_found", "Посылка не найдена")
            return self.view(row[0], row[1])

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

    async def list(
        self,
        owner: UUID,
        limit: int,
        type_id: int | None,
        calculated: bool | None,
        cursor: str | None,
    ) -> ParcelPage:
        query = select(Parcel, ParcelType.name).join(ParcelType).where(Parcel.owner_id == owner)
        if type_id is not None:
            query = query.where(Parcel.type_id == type_id)
        if calculated is not None:
            query = query.where(
                Parcel.delivery_cost_rub.is_not(None)
                if calculated
                else Parcel.delivery_cost_rub.is_(None)
            )
        if cursor:
            try:
                data = self.cursor.loads(cursor)
                if data["owner"] != str(owner) or data["filter"] != [type_id, calculated]:
                    raise ValueError("Cursor scope mismatch")
                query = query.where(
                    tuple_(Parcel.created_at, Parcel.id)
                    < tuple_(datetime.fromisoformat(data["time"]), UUID(data["id"]))
                )
            except (BadSignature, ValueError, KeyError, TypeError) as exc:
                raise Problem(422, "invalid_cursor", "Недействительный курсор") from exc
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    query.order_by(Parcel.created_at.desc(), Parcel.id.desc()).limit(limit + 1)
                )
            ).all()
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1][0]
            next_cursor = self.cursor.dumps(
                {
                    "owner": str(owner),
                    "filter": [type_id, calculated],
                    "time": last.created_at.isoformat(),
                    "id": str(last.id),
                }
            )
        return ParcelPage(
            items=[self.view(row[0], row[1]) for row in rows[:limit]], next_cursor=next_cursor
        )

    async def assignment_token(self, owner: UUID, parcel_id: UUID) -> str:
        token = secrets.token_urlsafe(32)
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(Parcel)
                .where(
                    Parcel.id == parcel_id, Parcel.owner_id == owner, Parcel.carrier_id.is_(None)
                )
                .values(assignment_token_hash=digest(token))
                .returning(Parcel.id)
            )
            if result.scalar_one_or_none() is None:
                await self.get(owner, parcel_id)
                raise Problem(409, "already_assigned", "Компания уже назначена")
        return token

    async def assign(self, parcel_id: UUID, token: str, company_id: int) -> int:
        """Claim an unassigned parcel atomically. A repeat from the winner is idempotent."""
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(Parcel)
                .where(
                    Parcel.id == parcel_id,
                    Parcel.assignment_token_hash == digest(token),
                    Parcel.carrier_id.is_(None),
                )
                .values(carrier_id=company_id)
                .returning(Parcel.carrier_id)
            )
            assigned = result.scalar_one_or_none()
            if assigned is not None:
                return assigned
            parcel = (
                await session.execute(
                    select(Parcel).where(
                        Parcel.id == parcel_id, Parcel.assignment_token_hash == digest(token)
                    )
                )
            ).scalar_one_or_none()
            if parcel is None:
                raise Problem(404, "parcel_not_found", "Посылка или ключ не найдены")
            if parcel.carrier_id != company_id:
                raise Problem(409, "already_assigned", "Посылка уже закреплена за другой компанией")
            return company_id
