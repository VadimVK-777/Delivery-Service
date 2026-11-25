import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from jinja2 import Template
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.exceptions import HTTPException

from delivery_service.db import Parcel, ParcelType
from delivery_service.observability import JsonFormatter
from delivery_service.schemas import Answer, ParcelMessage
from delivery_service.security import COOKIE
from delivery_service.services import PARCEL_TYPES_KEY, PARCEL_TYPES_TTL
from delivery_service.worker import calculate, parcel_message

DATA = {"name": "Laptop", "weight_kg": "2", "type_id": 2, "declared_value_usd": "100"}


async def seed(
    resources: Any,
    owner: UUID,
    *,
    status: str = "pending",
    type_id: int = 2,
    created_at: datetime | None = None,
) -> UUID:
    parcel_id = uuid4()
    async with resources.db.begin() as session:
        session.add(
            Parcel(
                id=parcel_id,
                owner_id=owner,
                name="Laptop",
                weight_kg=Decimal("2"),
                type_id=type_id,
                declared_value_usd=Decimal("100"),
                status=status,
                delivery_cost_rub=Decimal("180") if status == "calculated" else None,
                created_at=created_at or datetime.now(UTC),
            )
        )
    return parcel_id


async def owner_from_registration(client: Any, csrf: Any, resources: Any) -> UUID:
    response = await client.post("/api/v1/parcels", json=DATA, headers=csrf)
    assert response.status_code == 202
    return ParcelMessage.model_validate_json(resources.broker.publish.call_args.args[0]).owner_id


@pytest.mark.parametrize(
    ("method", "path", "route", "status"),
    [
        ("GET", "/health/live", "/health/live", 200),
        (
            "POST",
            "/api/v1/parcels/bad-id/assignment-token",
            "/api/v1/parcels/{parcel_id}/assignment-token",
            422,
        ),
        ("GET", "/missing", "unmatched", 404),
    ],
)
async def test_http_request_log(
    client: Any, caplog: pytest.LogCaptureFixture, method: str, path: str, route: str, status: int
) -> None:
    response = await client.request(method, path, params={"token": "private-query-value"})
    assert response.status_code == status
    (record,) = [record for record in caplog.records if record.getMessage() == "http_request"]
    entry = json.loads(JsonFormatter().format(record))
    assert (entry["method"], entry["route"], entry["status"]) == (method, route, status)
    assert entry["path"] == path and entry["status_code"] == status
    assert entry["request_id"] == response.headers["X-Request-ID"]
    assert "private-query-value" not in json.dumps(entry)


async def test_session_csrf_and_types(client: Any) -> None:
    assert (await client.get("/api/v1/parcels")).status_code == 401
    session = await client.get("/api/v1/session")
    assert "HttpOnly" in session.headers["set-cookie"]
    assert "SameSite=lax" in session.headers["set-cookie"]
    assert (
        await client.post("/api/v1/parcels", json=DATA, headers={"Idempotency-Key": "x"})
    ).status_code == 403
    assert len((await client.get("/api/v1/parcel-types")).json()) == 3
    assert (await client.get("/docs")).status_code == 200


async def test_parcel_types_cache_hit_and_expiry(client: Any, resources: Any) -> None:
    url = "/api/v1/parcel-types"
    expected = [
        {"id": 1, "name": "одежда"},
        {"id": 2, "name": "электроника"},
        {"id": 3, "name": "разное"},
    ]
    with patch.object(resources.parcels, "sessions", wraps=resources.db) as sessions:
        assert (await client.get(url)).json() == expected
        assert 0 < await resources.redis.ttl(PARCEL_TYPES_KEY) <= PARCEL_TYPES_TTL
        async with resources.db.begin() as session:
            (await session.get(ParcelType, 3)).name = "прочее"
        assert (await client.get(url)).json() == expected
        sessions.assert_called_once()
        await resources.redis.expire(PARCEL_TYPES_KEY, 0)
        expected[2]["name"] = "прочее"
        assert (await client.get(url)).json() == expected
        assert sessions.call_count == 2


@pytest.mark.parametrize("operation", ["get", "set"])
async def test_parcel_types_available_during_cache_failure(
    client: Any, resources: Any, operation: str
) -> None:
    with patch.object(resources.redis, operation, new=AsyncMock(side_effect=RedisConnectionError)):
        response = await client.get("/api/v1/parcel-types")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [1, 2, 3]


@pytest.mark.parametrize(("length", "status"), [(0, 422), (1, 202), (128, 202), (129, 422)])
async def test_idempotency_key_length(client: Any, csrf: Any, length: int, status: int) -> None:
    headers = {**csrf, "Idempotency-Key": "x" * length}
    response = await client.post("/api/v1/parcels", json=DATA, headers=headers)
    assert response.status_code == status


async def test_registration_idempotency_and_privacy(client: Any, csrf: Any, resources: Any) -> None:
    first = await client.post("/api/v1/parcels", json=DATA, headers=csrf)
    second = await client.post("/api/v1/parcels", json=DATA, headers=csrf)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert (await client.get(first.json()["status_url"])).json()["status"] == "queued"
    assert (
        await client.post("/api/v1/parcels", json={**DATA, "name": "Other"}, headers=csrf)
    ).status_code == 409
    # Only the worker is allowed to persist parcels.
    assert (await client.get("/api/v1/parcels")).json()["items"] == []
    client.cookies.clear()
    await client.get("/api/v1/session")
    assert (await client.get(first.json()["status_url"])).status_code == 404


async def test_publish_failure_retry_keeps_id(
    client: Any, csrf: Any, resources: Any, caplog: pytest.LogCaptureFixture
) -> None:
    resources.broker.publish.side_effect = OSError("offline")
    failure = await client.post("/api/v1/parcels", json=DATA, headers=csrf)
    assert failure.status_code == 503
    (record,) = [r for r in caplog.records if r.getMessage() == "broker_unavailable"]
    error = json.loads(JsonFormatter().format(record))
    assert error["level"] == "ERROR"
    assert error["request_id"] == failure.headers["X-Request-ID"]
    assert "OSError: offline" in error["traceback"]
    assert "offline" not in failure.text
    first = resources.broker.publish.call_args.args[1]
    resources.broker.publish.side_effect = None
    response = await client.post("/api/v1/parcels", json=DATA, headers=csrf)
    assert response.json()["id"] == first


async def test_validation_problem(client: Any, csrf: Any) -> None:
    response = await client.post("/api/v1/parcels", json={**DATA, "weight_kg": -1}, headers=csrf)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"]
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert (await client.get("/missing")).json()["status"] == 404


async def test_registration_limits(client: Any, csrf: Any, resources: Any) -> None:
    url = "/api/v1/parcels"
    for field, value in (("weight_kg", "30.001"), ("declared_value_usd", "10000.01")):
        response = await client.post(url, json={**DATA, field: value}, headers=csrf)
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == ["body", field]
    resources.broker.publish.assert_not_awaited()
    response = await client.post(
        url, json={**DATA, "weight_kg": "30.000", "declared_value_usd": "10000.00"}, headers=csrf
    )
    assert response.status_code == 202
    resources.broker.publish.assert_awaited_once()


async def test_previously_accepted_oversized_parcel(client: Any, csrf: Any, resources: Any) -> None:
    await owner_from_registration(client, csrf, resources)
    queued = json.loads(resources.broker.publish.call_args.args[0])
    queued["parcel"].update(weight_kg="31.000", declared_value_usd="10001.00")
    message = ParcelMessage.model_validate(queued)
    rates = AsyncMock()
    rates.get.return_value = (Decimal("90"), "2026-01-01")
    await calculate(message, resources.db, rates)
    async with resources.db() as session:
        stored = await session.get(Parcel, message.id)
        assert parcel_message(stored).parcel == message.parcel
    detail = await client.get(f"/api/v1/parcels/{message.id}")
    assert detail.status_code == 200
    assert detail.json()["weight_kg"] == "31.000"
    assert detail.json()["declared_value_usd"] == "10001.00"


async def test_list_filter_cursor_and_owner(client: Any, csrf: Any, resources: Any) -> None:
    owner = await owner_from_registration(client, csrf, resources)
    now = datetime.now(UTC)
    ids = [
        await seed(
            resources,
            owner,
            status=status,
            type_id=i % 3 + 1,
            created_at=now + timedelta(seconds=i),
        )
        for i, status in enumerate(("pending", "calculated", "pending", "calculated", "failed"))
    ]
    await seed(resources, uuid4())
    first = (await client.get("/api/v1/parcels?limit=2")).json()
    second = (
        await client.get("/api/v1/parcels", params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert len({p["id"] for p in first["items"] + second["items"]}) == 4
    calculated = (await client.get("/api/v1/parcels?type_id=2&calculated=true")).json()["items"]
    assert [p["id"] for p in calculated] == [str(ids[1])]
    assert calculated[0]["delivery_cost_rub"] == "180.00"
    uncalculated = (await client.get("/api/v1/parcels?calculated=false")).json()["items"]
    assert [p["id"] for p in uncalculated] == [str(ids[i]) for i in (4, 2, 0)]
    assert len((await client.get("/api/v1/parcels?type_id=3")).json()["items"]) == 1
    assert (await client.get(f"/api/v1/parcels/{ids[0]}")).json()[
        "delivery_cost_display"
    ] == "Не рассчитано"
    assert (await client.get(f"/api/v1/registrations/{ids[0]}")).json()["status"] == "pending"
    assert (await client.get("/api/v1/parcels?cursor=garbage")).status_code == 422
    assert (
        await client.get("/api/v1/parcels", params={"cursor": first["next_cursor"], "type_id": 1})
    ).status_code == 422
    client.cookies.clear()
    await client.get("/api/v1/session")
    assert (await client.get(f"/api/v1/parcels/{ids[0]}")).status_code == 404
    assert (await client.get(f"/api/v1/registrations/{ids[0]}")).status_code == 404
    assert (await client.get("/api/v1/parcels")).json()["items"] == []


async def test_carrier_token_rotation_and_conflict(client: Any, csrf: Any, resources: Any) -> None:
    owner = await owner_from_registration(client, csrf, resources)
    parcel_id = await seed(resources, owner)
    url = f"/api/v1/parcels/{parcel_id}"
    old = (await client.post(url + "/assignment-token", headers=csrf)).json()["assignment_token"]
    token = (await client.post(url + "/assignment-token", headers=csrf)).json()["assignment_token"]
    assert (
        await client.put(
            url + "/carrier", json={"company_id": 1}, headers={"X-Assignment-Token": old}
        )
    ).status_code == 404
    headers = {"X-Assignment-Token": token}
    assert (
        await client.put(url + "/carrier", json={"company_id": 1}, headers=headers)
    ).status_code == 200
    assert (
        await client.put(url + "/carrier", json={"company_id": 1}, headers=headers)
    ).status_code == 200
    assert (
        await client.put(url + "/carrier", json={"company_id": 2}, headers=headers)
    ).status_code == 409
    assert (await client.post(url + "/assignment-token", headers=csrf)).status_code == 409
    assert (
        await client.put(url + "/carrier", json={"company_id": 0}, headers=headers)
    ).status_code == 422


async def test_support_limit(client: Any, csrf: Any, resources: Any) -> None:
    resources.support.answer.return_value = Answer(answer="Ответ", sources=["rules.md#1"])
    for _ in range(10):
        assert (
            await client.post(
                "/api/v1/support/questions", json={"question": "Ноутбук?"}, headers=csrf
            )
        ).status_code == 200
    assert (
        await client.post("/api/v1/support/questions", json={"question": "Ноутбук?"}, headers=csrf)
    ).status_code == 429


async def test_home_renders_once_and_reuses_session(client: Any) -> None:
    with patch.object(Template, "render", autospec=True, side_effect=Template.render) as render:
        first = await client.get("/")
        render.assert_called_once()
        render.reset_mock()
        second = await client.get("/")
        render.assert_called_once()

    token = (await client.get("/api/v1/session")).json()["csrf_token"]
    for page in (first, second):
        assert page.status_code == 200
        assert page.cookies[COOKIE]
        assert len(page.headers.get_list("set-cookie")) == 1
        assert page.headers["cache-control"] == "no-store"
        assert f'<meta name="csrf-token" content="{token}">' in page.text


async def test_html_health_and_metrics(client: Any, csrf: Any, resources: Any) -> None:
    assert "PARCEL" in (await client.get("/")).text
    assert (await client.get("/static/app.js")).status_code == 200
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).status_code == 200
    assert "delivery_http" in (await client.get("/metrics")).text
    assert (await client.get("/web/parcels?type_id=999")).status_code == 422
    owner = await owner_from_registration(client, csrf, resources)
    parcel_id = await seed(resources, owner)
    assert "Laptop" in (await client.get("/web/parcels")).text
    assert (await client.get(f"/parcels/{parcel_id}")).status_code == 200


@pytest.mark.parametrize(
    ("error_type", "status", "event"),
    [(RedisConnectionError, 503, "dependency_unavailable"), (RuntimeError, 500, "unhandled_error")],
)
async def test_internal_errors_redacted(
    client: Any,
    resources: Any,
    caplog: pytest.LogCaptureFixture,
    error_type: type[Exception],
    status: int,
    event: str,
) -> None:
    error = error_type("Connection refused: redis://app:secret-pass@redis:6379/0?token=url-secret")
    resources.redis.ping = AsyncMock(side_effect=error)
    result = await client.get("/health/ready?token=request-secret")
    assert result.status_code == status
    request_id = result.headers["X-Request-ID"]
    assert request_id == result.json()["request_id"]
    assert "Connection refused" not in result.text

    logs = {
        record.getMessage(): json.loads(JsonFormatter().format(record))
        for record in caplog.records
        if record.name == "delivery_service.main"
    }
    for entry in (logs[event], logs["http_request"]):
        assert entry["request_id"] == request_id
        assert entry["method"] == "GET" and entry["path"] == "/health/ready"
    assert logs["http_request"]["status_code"] == status
    assert logs["http_request"]["status"] == status
    assert logs["http_request"]["route"] == "/health/ready"
    assert logs[event]["exception_type"] == error_type.__name__
    assert logs[event]["exception_message"] == "Connection refused: redis://***@redis:6379/0"
    assert "Traceback (most recent call last)" in logs[event]["traceback"]
    assert "in ready" in logs[event]["traceback"]
    for secret in ("secret-pass", "url-secret", "request-secret"):
        assert secret not in json.dumps(logs)


async def test_wrapped_dependency_error_logs_cause(
    client: Any, resources: Any, caplog: pytest.LogCaptureFixture
) -> None:
    resources.broker.connect.side_effect = OSError("connection refused on rabbitmq:5672")
    result = await client.get("/health/ready")
    assert result.status_code == 503
    record = next(r for r in caplog.records if r.getMessage() == "broker_unavailable")
    entry = json.loads(JsonFormatter().format(record))
    assert entry["request_id"] == result.headers["X-Request-ID"]
    assert "OSError: connection refused on rabbitmq:5672" in entry["traceback"]
    assert "connection refused" not in result.text


async def test_http_exception_logs_server_error(
    client: Any, resources: Any, caplog: pytest.LogCaptureFixture
) -> None:
    resources.redis.ping = AsyncMock(side_effect=HTTPException(503, "Temporarily unavailable"))
    result = await client.get("/health/ready")
    assert result.status_code == 503
    record = next(r for r in caplog.records if r.getMessage() == "http_error")
    error = json.loads(JsonFormatter().format(record))
    assert error["request_id"] == result.headers["X-Request-ID"]
    assert error["method"] == "GET" and error["path"] == "/health/ready"
    assert error["exception_type"] == "HTTPException"
    assert "Temporarily unavailable" in error["exception_message"]
    assert "in ready" in error["traceback"]
