from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from delivery_service.db import Base, ParcelType, database
from delivery_service.main import create_app
from delivery_service.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, session_secret="unit-test-secret-with-more-than-32-characters")


@pytest.fixture
async def db(tmp_path: Any) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine, sessions = database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions.begin() as session:
        session.add_all(
            [
                ParcelType(id=i, name=name)
                for i, name in enumerate(["одежда", "электроника", "разное"], 1)
            ]
        )
    yield sessions
    await engine.dispose()


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def resources(db: Any, redis: Any, settings: Settings) -> SimpleNamespace:
    return SimpleNamespace(
        db=db,
        redis=redis,
    )


@pytest.fixture
async def client(settings: Settings, resources: Any) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, resources)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.get("/api/v1/session")
    return {"X-CSRF-Token": response.json()["csrf_token"], "Idempotency-Key": "test-key"}
