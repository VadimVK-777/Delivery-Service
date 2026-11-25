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

    @router.get("/session", tags=["Session"], summary="Открыть или продлить сессию")
    async def session_info(request: Request, response: Response) -> dict[str, str]:
        """Устанавливает подписанную cookie `delivery_session` и возвращает `csrf_token`.

        Сохраняйте cookie между запросами. Для регистрации посылки, выпуска ключа
        перевозчика и обращения в поддержку передавайте токен в заголовке `X-CSRF-Token`.
        """
        owner = security.get_or_create_owner(request)
        security.set_cookie(response, owner)
        return {"csrf_token": security.csrf(owner)}

    @router.get(
        "/parcel-types",
        response_model=list[ParcelTypeView],
        tags=["Parcels"],
        summary="Получить справочник типов посылок",
    )
    async def types(request: Request) -> Any:
        """Возвращает идентификатор и название каждого типа. Используйте `id` как
        `type_id` при регистрации посылки или фильтрации списка.
        """
        return await request.app.state.resources.parcels.list_types()

    @router.post(
        "/parcels",
        status_code=202,
        response_model=Registration,
        tags=["Parcels"],
        summary="Зарегистрировать посылку",
    )
    async def register(
        request: Request,
        data: ParcelInput,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> Any:
        """Передайте cookie сессии, `X-CSRF-Token` и уникальный `Idempotency-Key`.
        Ответ `202` подтверждает постановку в очередь. Ход расчёта доступен по `status_url`.

        Ключ действует 24 часа в пределах сессии. Повтор с тем же ключом и данными возвращает
        тот же идентификатор посылки. Если данные изменились, сервис возвращает `409`.
        """
        owner = security.require(request, mutate=True)
        return await request.app.state.resources.parcels.register(owner, data, idempotency_key)

    @router.get(
        "/registrations/{parcel_id}",
        response_model=Registration,
        tags=["Parcels"],
        summary="Проверить статус регистрации",
    )
    async def registration_status(request: Request, parcel_id: UUID) -> Any:
        """Возвращает статус посылки текущей сессии. Пока сообщение передаётся в очередь,
        статус может быть `publishing` или `queued`. После сохранения посылки доступны
        `pending`, `calculated` и `failed`.
        """
        return await request.app.state.resources.parcels.status(
            security.require(request), parcel_id
        )

    @router.get(
        "/parcels",
        response_model=ParcelPage,
        tags=["Parcels"],
        summary="Получить список своих посылок",
    )
    async def parcel_list(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        type_id: Annotated[int | None, Query(ge=1, le=3)] = None,
        calculated: bool | None = None,
        cursor: str | None = None,
    ) -> Any:
        """Возвращает сохранённые посылки текущей сессии от новых к старым.
        Фильтр `type_id` задаёт тип, `calculated=true` выбирает рассчитанные посылки,
        а `calculated=false` включает статусы `pending` и `failed`.

        Для следующей страницы передайте `next_cursor` как `cursor` с теми же фильтрами.
        Значение `null` в `next_cursor` означает конец списка.
        """
        return await request.app.state.resources.parcels.list(
            security.require(request), limit, type_id, calculated, cursor
        )

    @router.get(
        "/parcels/{parcel_id}",
        response_model=ParcelView,
        tags=["Parcels"],
        summary="Получить карточку своей посылки",
    )
    async def parcel_detail(request: Request, parcel_id: UUID) -> Any:
        """Возвращает параметры посылки текущей сессии, статус и стоимость доставки в рублях.
        До завершения расчёта поле `delivery_cost_rub` содержит `null`.
        """
        return await request.app.state.resources.parcels.get(security.require(request), parcel_id)

    @router.post(
        "/parcels/{parcel_id}/assignment-token",
        tags=["Carrier"],
        summary="Выпустить ключ назначения перевозчика",
    )
    async def assignment_token(
        request: Request,
        parcel_id: UUID,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        """Владелец посылки передаёт cookie сессии и `X-CSRF-Token`, чтобы получить
        `assignment_token` для перевозчика. Повторный выпуск заменяет предыдущий ключ.
        После назначения перевозчика сервис возвращает `409`.
        """
        token = await request.app.state.resources.parcels.assignment_token(
            security.require(request, mutate=True), parcel_id
        )
        return {"assignment_token": token}

    @router.put(
        "/parcels/{parcel_id}/carrier", tags=["Carrier"], summary="Назначить перевозчика посылки"
    )
    async def assign_carrier(
        request: Request,
        parcel_id: UUID,
        data: CarrierInput,
        x_assignment_token: Annotated[str, Header(min_length=32, max_length=128)],
    ) -> dict[str, int]:
        """Перевозчик передаёт ключ владельца в заголовке `X-Assignment-Token`
        и свой `company_id` в теле запроса. Повтор с тем же ключом и компанией возвращает
        её идентификатор. Попытка назначить другую компанию с действующим ключом даёт `409`.
        """
        company = await request.app.state.resources.parcels.assign(
            parcel_id, x_assignment_token, data.company_id
        )
        return {"company_id": company}

    @router.post(
        "/support/questions",
        response_model=Answer,
        tags=["Support"],
        summary="Задать вопрос поддержке",
    )
    async def support_question(
        request: Request,
        data: Question,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> Any:
        """Передайте вопрос о доставке с cookie сессии и заголовком `X-CSRF-Token`.
        Ответ содержит текст и список источников. Лимит одной сессии составляет
        10 вопросов в минуту. При превышении сервис возвращает `429`.
        """
        owner = security.require(request, mutate=True)
        resource = request.app.state.resources
        count = await resource.redis.eval(LIMIT, 1, f"support:limit:{owner}")
        if count > 10:
            raise Problem(429, "rate_limit", "Не более 10 вопросов в минуту")
        return await resource.support.answer(data.question)

    return router
