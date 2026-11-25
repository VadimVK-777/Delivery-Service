"""Public HTTP API for sessions, parcels, carriers and support."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response

from delivery_service.domain import Problem
from delivery_service.schemas import (
    Answer,
    CarrierInput,
    ParcelInput,
    ParcelPage,
    ParcelTypeView,
    ParcelView,
    Question,
    Registration,
)
from delivery_service.security import Sessions

LIMIT = (
    "local n=redis.call('incr',KEYS[1]); if n==1 then redis.call('expire',KEYS[1],60) end; return n"
)


def create_router(security: Sessions) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/session", tags=["Session"])
    async def session_info(request: Request, response: Response) -> dict[str, str]:
        owner = security.get_or_create_owner(request)
        security.set_cookie(response, owner)
        return {"csrf_token": security.csrf(owner)}

    @router.get("/parcel-types", response_model=list[ParcelTypeView], tags=["Parcels"])
    async def types(request: Request) -> Any:
        return await request.app.state.resources.parcels.list_types()

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

    @router.get("/parcels", response_model=ParcelPage, tags=["Parcels"])
    async def parcel_list(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        type_id: Annotated[int | None, Query(ge=1, le=3)] = None,
        calculated: bool | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await request.app.state.resources.parcels.list(
            security.require(request), limit, type_id, calculated, cursor
        )

    @router.get("/parcels/{parcel_id}", response_model=ParcelView, tags=["Parcels"])
    async def parcel_detail(request: Request, parcel_id: UUID) -> Any:
        return await request.app.state.resources.parcels.get(security.require(request), parcel_id)

    @router.post("/parcels/{parcel_id}/assignment-token", tags=["Carrier"])
    async def assignment_token(
        request: Request,
        parcel_id: UUID,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        token = await request.app.state.resources.parcels.assignment_token(
            security.require(request, mutate=True), parcel_id
        )
        return {"assignment_token": token}

    @router.put("/parcels/{parcel_id}/carrier", tags=["Carrier"])
    async def assign_carrier(
        request: Request,
        parcel_id: UUID,
        data: CarrierInput,
        x_assignment_token: Annotated[str, Header(min_length=32, max_length=128)],
    ) -> dict[str, int]:
        company = await request.app.state.resources.parcels.assign(
            parcel_id, x_assignment_token, data.company_id
        )
        return {"company_id": company}

    @router.post("/support/questions", response_model=Answer, tags=["Support"])
    async def support_question(
        request: Request,
        data: Question,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> Any:
        owner = security.require(request, mutate=True)
        resource = request.app.state.resources
        count = await resource.redis.eval(LIMIT, 1, f"support:limit:{owner}")
        if count > 10:
            raise Problem(429, "rate_limit", "Не более 10 вопросов в минуту")
        return await resource.support.answer(data.question)

    return router
