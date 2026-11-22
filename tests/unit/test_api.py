from typing import Any

DATA = {"name": "Laptop", "weight_kg": "2", "type_id": 2, "declared_value_usd": "100"}


async def test_session_csrf_and_types(client: Any) -> None:
    session = await client.get("/api/v1/session")
    assert "HttpOnly" in session.headers["set-cookie"]
    assert "SameSite=lax" in session.headers["set-cookie"]
    assert len((await client.get("/api/v1/parcel-types")).json()) == 3
    assert (await client.get("/docs")).status_code == 200
