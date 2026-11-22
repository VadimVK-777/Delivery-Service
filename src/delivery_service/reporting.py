"""Transactional immutable calculation log and striped daily rollups."""

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError
from sqlalchemy import select

from delivery_service.db import Outbox, database, utcnow
from delivery_service.settings import Settings

logger = logging.getLogger(__name__)


class Reports:
    def __init__(self, url: str) -> None:
        self.client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            url, serverSelectionTimeoutMS=5000, tz_aware=True
        )
        self.db = self.client.delivery

    async def initialize(self) -> None:
        await self.db.calculations.create_index([("day", 1), ("type_id", 1)])
        await self.db.calculations.create_index("calculated_at")
        await self.db.daily.create_index([("day", 1), ("type_id", 1), ("bucket", 1)], unique=True)

    async def record(self, payload: dict[str, Any]) -> None:
        """Log and increment together. A repeated event cannot increment twice."""
        event_id = payload["event_id"]
        bucket = int(hashlib.sha256(event_id.encode()).hexdigest(), 16) % 64
        document = {**payload, "_id": event_id}
        timestamp = datetime.fromisoformat(payload["calculated_at"])
        # BSON datetime precision is milliseconds. Normalize before duplicate comparison.
        document["calculated_at"] = timestamp.replace(
            microsecond=(timestamp.microsecond // 1000) * 1000
        )
        async with self.client.start_session() as session:

            async def commit(transaction: Any) -> None:
                await self.db.calculations.insert_one(document, session=transaction)
                await self.db.daily.update_one(
                    {"day": payload["day"], "type_id": payload["type_id"], "bucket": bucket},
                    {"$inc": {"amount_kopecks": payload["amount_kopecks"], "count": 1}},
                    upsert=True,
                    session=transaction,
                )

            try:
                await session.with_transaction(commit)
            except DuplicateKeyError:
                existing = await self.db.calculations.find_one({"_id": event_id})
                if existing is None:
                    raise  # A concurrent bucket upsert: retry the entire event later.
                if any(existing.get(key) != value for key, value in document.items()):
                    raise ValueError("Conflicting payload for existing calculation event") from None

    async def daily(self, day: str, *, raw: bool = False) -> list[dict[str, Any]]:
        """Sum kopecks and counts per type for a Moscow date, optionally from the event log."""
        collection = self.db.calculations if raw else self.db.daily
        cursor = await collection.aggregate(
            [
                {"$match": {"day": day}},
                {
                    "$group": {
                        "_id": "$type_id",
                        "amount_kopecks": {"$sum": "$amount_kopecks"},
                        "count": {"$sum": 1 if raw else "$count"},
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
        return await cursor.to_list(length=None)

    async def close(self) -> None:
        await self.client.close()


async def run(settings: Settings, *, once: bool = False) -> None:
    engine, sessions = database(settings.database_url)
    reports = Reports(settings.mongo_url.get_secret_value())
    try:
        await reports.initialize()
        while True:
            try:
                async with sessions.begin() as session:
                    events = (
                        await session.scalars(
                            select(Outbox)
                            .where(Outbox.delivered_at.is_(None))
                            .order_by(Outbox.created_at)
                            .limit(50)
                            .with_for_update(skip_locked=True)
                        )
                    ).all()
                    for event in events:
                        await reports.record(event.payload)
                        event.delivered_at = utcnow()
                if once:
                    return
                if not events:
                    await asyncio.sleep(1)
            except Exception:
                logger.exception("outbox_delivery_failed")
                if once:
                    raise
                await asyncio.sleep(5)
    finally:
        await reports.close()
        await engine.dispose()
