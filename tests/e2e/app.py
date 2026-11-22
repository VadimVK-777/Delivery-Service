"""Browser harness with real persistence and broker, deterministic external currency API."""

from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

from delivery_service.main import Resources, create_app
from delivery_service.schemas import Answer
from delivery_service.settings import Settings
from delivery_service.worker import consume

settings = Settings()
resources = Resources(settings)
resources.support.answer = AsyncMock(
    return_value=Answer(
        answer="Ноутбук можно отправить согласно демонстрационным правилам.", sources=["rules.md#2"]
    )
)
app = create_app(settings, resources)


@asynccontextmanager
async def lifespan(application: Any) -> Any:
    await resources.broker.connect()
    rates = AsyncMock()
    rates.get.return_value = (Decimal("90"), "2026-01-01")

    async def handler(incoming: Any) -> None:
        await consume(incoming, resources.db, rates, resources.broker)

    await resources.broker.queue.consume(handler)
    yield
    await resources.close()


app.router.lifespan_context = lifespan
