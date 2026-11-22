"""Local browser verification with SQLite and deterministic infrastructure adapters."""

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fakeredis.aioredis import FakeRedis

from delivery_service.db import Base, ParcelType, database
from delivery_service.main import create_app
from delivery_service.schemas import Answer, ParcelMessage
from delivery_service.services import Parcels
from delivery_service.settings import Settings
from delivery_service.worker import calculate

settings = Settings(
    _env_file=None, session_secret="browser-local-test-secret-at-least-32-characters"
)
engine, sessions = database("sqlite+aiosqlite:///:memory:")
redis = FakeRedis(decode_responses=True)
rates = AsyncMock()
rates.get.return_value = (Decimal("90"), "2026-01-01")


class LocalBroker:
    async def connect(self) -> None:
        pass

    async def publish(self, body: bytes, message_id: str) -> None:
        await calculate(ParcelMessage.model_validate_json(body), sessions, rates)


resources = SimpleNamespace(db=sessions, redis=redis, broker=LocalBroker(), support=AsyncMock())
resources.parcels = Parcels(
    sessions, redis, resources.broker, settings.session_secret.get_secret_value()
)
resources.support.answer.return_value = Answer(
    answer="Ноутбук можно отправить согласно демонстрационным правилам.", sources=["rules.md#2"]
)
app = create_app(settings, resources)


@asynccontextmanager
async def lifespan(application: Any) -> Any:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions.begin() as session:
        session.add_all(
            [ParcelType(id=i, name=n) for i, n in enumerate(["одежда", "электроника", "разное"], 1)]
        )
    yield
    await redis.aclose()
    await engine.dispose()


app.router.lifespan_context = lifespan
