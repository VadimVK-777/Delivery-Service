from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from delivery_service.db import Parcel
from delivery_service.schemas import Answer, ParcelMessage

DATA = {"name": "Laptop", "weight_kg": "2", "type_id": 2, "declared_value_usd": "100"}


async def seed(
    resources: Any,
    owner: UUID,
    *,
    calculated: bool = False,
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
                status="calculated" if calculated else "pending",
                delivery_cost_rub=Decimal("180") if calculated else None,
                created_at=created_at or datetime.now(UTC),
            )
        )
    return parcel_id


async def owner_from_registration(client: Any, csrf: Any, resources: Any) -> UUID:
    response = await client.post("/api/v1/parcels", json=DATA, headers=csrf)
    assert response.status_code == 202
    return ParcelMessage.model_validate_json(resources.broker.publish.call_args.args[0]).owner_id


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


async def test_publish_failure_retry_keeps_id(client: Any, csrf: Any, resources: Any) -> None:
    resources.broker.publish.side_effect = OSError("offline")
    assert (await client.post("/api/v1/parcels", json=DATA, headers=csrf)).status_code == 503
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


async def test_list_filter_cursor_and_owner(client: Any, csrf: Any, resources: Any) -> None:
    owner = await owner_from_registration(client, csrf, resources)
    now = datetime.now(UTC)
    ids = [
        await seed(
            resources,
            owner,
            calculated=bool(i % 2),
            type_id=i % 3 + 1,
            created_at=now + timedelta(seconds=i),
        )
        for i in range(5)
    ]
    await seed(resources, uuid4())
    first = (await client.get("/api/v1/parcels?limit=2")).json()
    second = (
        await client.get("/api/v1/parcels", params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert len({p["id"] for p in first["items"] + second["items"]}) == 4
    assert (await client.get("/api/v1/parcels?calculated=true")).json()["items"][0][
        "delivery_cost_rub"
    ] == "180.00"
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
