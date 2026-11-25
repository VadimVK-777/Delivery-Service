"""HTML pages and HTMX fragments for parcels."""

from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from delivery_service.domain import Problem
from delivery_service.security import Sessions


def create_router(security: Sessions, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        owner = security.get_or_create_owner(request)
        response = templates.TemplateResponse(
            request=request, name="index.html", context={"csrf": security.csrf(owner)}
        )
        security.set_cookie(response, owner)
        return response

    @router.get("/web/parcels", response_class=HTMLResponse)
    async def parcel_fragment(
        request: Request,
        type_id: str = "",
        calculated: str = "",
        cursor: str | None = None,
    ) -> Response:
        if type_id not in ("", "1", "2", "3") or calculated not in ("", "true", "false"):
            raise Problem(422, "invalid_filter", "Неверный фильтр")
        page = await request.app.state.resources.parcels.list(
            security.require(request),
            20,
            int(type_id) if type_id else None,
            None if not calculated else calculated == "true",
            cursor,
        )
        return templates.TemplateResponse(
            request=request, name="parcels.html", context={"page": page}
        )

    @router.get("/parcels/{parcel_id}", response_class=HTMLResponse)
    async def detail_page(request: Request, parcel_id: UUID) -> Response:
        owner = security.require(request)
        parcel = await request.app.state.resources.parcels.get(owner, parcel_id)
        return templates.TemplateResponse(
            request=request,
            name="detail.html",
            context={
                "parcel": parcel,
                "csrf": security.csrf(owner),
            },
        )

    return router
