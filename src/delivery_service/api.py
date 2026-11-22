"""Public HTTP API for sessions, parcels, carriers and support."""

from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from delivery_service.db import ParcelType
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

    return router
