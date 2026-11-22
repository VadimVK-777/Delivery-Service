"""Public HTTP API for sessions, parcels, carriers and support."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from sqlalchemy import select

from delivery_service.db import ParcelType
from delivery_service.schemas import (
    ParcelInput,
    Registration,
)
from delivery_service.security import Sessions


def create_router(security: Sessions) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/session", tags=["Session"])
    async def session_info(request: Request, response: Response) -> dict[str, str]:
        owner = security.initialize(request, response)
        return {"csrf_token": security.csrf(owner)}

    @router.get("/parcel-types", tags=["Parcels"])
    async def types(request: Request) -> list[dict[str, Any]]:
        async with request.app.state.resources.db() as session:
            rows = (await session.scalars(select(ParcelType).order_by(ParcelType.id))).all()
            return [{"id": row.id, "name": row.name} for row in rows]

    @router.post("/parcels", status_code=202, response_model=Registration, tags=["Parcels"])
    async def register(
        request: Request,
        data: ParcelInput,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> Any:
        owner = security.require(request, mutate=True)
        return await request.app.state.resources.parcels.register(owner, data, idempotency_key)

    @router.get("/registrations/{parcel_id}", response_model=Registration, tags=["Parcels"])
    async def registration_status(request: Request, parcel_id: UUID) -> Any:
        return await request.app.state.resources.parcels.status(
            security.require(request), parcel_id
        )

    return router
