import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis.asyncio import Redis

from delivery_service.db import database
from delivery_service.settings import Settings


@pytest.fixture
def settings() -> Settings:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Use make test-integration (isolated Docker Compose stack)")
    return Settings()


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[Any]:
    engine, sessions = database(settings.database_url)
    yield sessions
    await engine.dispose()


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Any]:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    yield redis
    await redis.aclose()
