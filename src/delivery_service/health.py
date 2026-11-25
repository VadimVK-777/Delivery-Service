"""Service health checks and Prometheus metrics."""

import os
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, generate_latest
from prometheus_client.multiprocess import MultiProcessCollector
from sqlalchemy import func, select, text

from delivery_service.db import Outbox
from delivery_service.domain import Problem
from delivery_service.observability import OUTBOX_AGE, OUTBOX_PENDING

router = APIRouter(include_in_schema=False)


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    resource = request.app.state.resources
    async with resource.db() as session:
        await session.execute(text("SELECT 1"))
    await resource.redis.ping()
    try:
        await resource.broker.connect()
    except Exception as exc:
        raise Problem(503, "broker_unavailable", "Брокер недоступен") from exc
    return {"status": "ok"}


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    async with request.app.state.resources.db() as session:
        count, oldest = (
            await session.execute(
                select(func.count(), func.min(Outbox.created_at)).where(
                    Outbox.delivered_at.is_(None)
                )
            )
        ).one()
    OUTBOX_PENDING.set(count)
    OUTBOX_AGE.set(
        (datetime.now(UTC) - oldest.replace(tzinfo=UTC)).total_seconds() if oldest else 0
    )
    registry = REGISTRY
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    return Response(generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})
