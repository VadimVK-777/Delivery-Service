from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pymongo.errors import DuplicateKeyError

from delivery_service.reporting import Reports


def event() -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "parcel_id": str(uuid4()),
        "type_id": 2,
        "amount_kopecks": 18000,
        "day": "2026-01-01",
        "calculated_at": datetime(2026, 1, 1, microsecond=123456, tzinfo=UTC).isoformat(),
    }


async def test_mongo_transaction_and_duplicate_conflict() -> None:
    reports = Reports("mongodb://localhost:27017")
    await reports.client.close()
    db = SimpleNamespace(calculations=AsyncMock(), daily=AsyncMock())
    reports.db = db
    session = AsyncMock()

    async def transaction(callback: Any) -> None:
        await callback(session)

    session.with_transaction.side_effect = transaction
    client = MagicMock()
    client.start_session.return_value.__aenter__.return_value = session
    client.close = AsyncMock()
    reports.client = client
    payload = event()
    await reports.initialize()
    await reports.record(payload)
    assert db.daily.update_one.call_args.kwargs["session"] is session
    assert db.daily.update_one.call_args.args[1]["$inc"]["amount_kopecks"] == 18000
    document = db.calculations.insert_one.call_args.args[0]
    assert document["calculated_at"].microsecond == 123000
    db.calculations.insert_one.side_effect = DuplicateKeyError("duplicate")
    db.calculations.find_one.return_value = document
    await reports.record(payload)
    assert db.daily.update_one.await_count == 1
    db.calculations.find_one.return_value = {**document, "amount_kopecks": 1}
    with pytest.raises(ValueError, match="Conflicting"):
        await reports.record(payload)
    db.calculations.find_one.return_value = None
    with pytest.raises(DuplicateKeyError):
        await reports.record(payload)
    cursor = AsyncMock()
    cursor.to_list.return_value = [{"_id": 2, "amount_kopecks": 18000, "count": 1}]
    db.daily.aggregate.return_value = cursor
    db.calculations.aggregate.return_value = cursor
    assert await reports.daily("2026-01-01") == await reports.daily("2026-01-01", raw=True)
    await reports.close()
