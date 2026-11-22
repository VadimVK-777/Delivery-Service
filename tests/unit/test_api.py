from typing import Any

DATA = {"name": "Laptop", "weight_kg": "2", "type_id": 2, "declared_value_usd": "100"}


async def test_session_csrf_and_types(client: Any) -> None:
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
