"""Create the FastAPI application and manage its shared resources."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from delivery_service import api
from delivery_service.broker import Broker
from delivery_service.db import database
from delivery_service.domain import Problem
from delivery_service.security import Sessions
from delivery_service.services import Parcels
from delivery_service.settings import Settings

logger = logging.getLogger(__name__)


class Resources:
    def __init__(self, settings: Settings) -> None:
        self.engine, self.db = database(settings.database_url)
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self.http = httpx.AsyncClient(timeout=5)
        self.broker = Broker(settings)
        self.parcels = Parcels(
            self.db, self.redis, self.broker, settings.session_secret.get_secret_value()
        )

    async def close(self) -> None:
        await self.broker.close()
        await self.http.aclose()
        await self.redis.aclose()
        await self.engine.dispose()


def create_app(settings: Settings | None = None, resources: Any = None) -> FastAPI:
    settings = settings or Settings()
    security = Sessions(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.resources = resources or Resources(settings)
        yield
        if resources is None:
            await application.state.resources.close()

    app = FastAPI(
        title="International Delivery",
        version="1.0.0",
        lifespan=lifespan,
        description="Анонимные сессии и асинхронная регистрация посылок.",
    )
    if resources is not None:
        app.state.resources = resources

    def problem_response(request: Request, error: Problem, **extra: Any) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            media_type="application/problem+json",
            content={
                "type": f"urn:delivery:error:{error.code}",
                "title": error.code,
                "status": error.status,
                "detail": error.detail,
                "code": error.code,
                "instance": request.url.path,
                "request_id": getattr(request.state, "request_id", ""),
                **extra,
            },
        )

    @app.exception_handler(Problem)
    async def problem_handler(request: Request, error: Problem) -> JSONResponse:
        return problem_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, error: RequestValidationError) -> JSONResponse:
        return problem_response(
            request,
            Problem(422, "validation_error", "Проверьте входные данные"),
            errors=[{"field": list(e["loc"]), "message": e["msg"]} for e in error.errors()],
        )

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, error: HTTPException) -> JSONResponse:
        return problem_response(
            request, Problem(error.status_code, "http_error", str(error.detail))
        )

    @app.middleware("http")
    async def instrumentation(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = str(uuid4())
        try:
            response = await call_next(request)
        except (RedisError, SQLAlchemyError):
            logger.exception("dependency_unavailable")
            response = problem_response(
                request, Problem(503, "dependency_unavailable", "Сервис временно недоступен")
            )
        except Exception:
            logger.exception("unhandled_error")
            response = problem_response(
                request, Problem(500, "internal_error", "Внутренняя ошибка")
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(api.create_router(security))
    return app
