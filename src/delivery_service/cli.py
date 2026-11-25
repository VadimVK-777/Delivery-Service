"""Operational commands; financial reports are intentionally not public HTTP routes."""

import argparse
import asyncio
import json
from datetime import date
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select

from delivery_service.db import Outbox, database
from delivery_service.observability import configure
from delivery_service.settings import Settings


async def execute(args: argparse.Namespace, settings: Settings) -> None:
    if args.command == "worker":
        from delivery_service.worker import run

        await run(settings)
    elif args.command == "outbox":
        from delivery_service.reporting import run as outbox_run

        await outbox_run(settings, once=args.once)
    elif args.command == "retry-parcel":
        from delivery_service.worker import retry_parcel

        await retry_parcel(UUID(args.id), settings)
    elif args.command == "index-knowledge":
        from delivery_service.rag import Support

        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        support = Support(settings, redis)
        try:
            print(await support.index())
        finally:
            await support.close()
            await redis.aclose()
    elif args.command in ("report-daily", "verify-report"):
        from delivery_service.reporting import Reports

        date.fromisoformat(args.date)
        reports = Reports(settings.mongo_url.get_secret_value())
        engine, sessions = database(settings.database_url)
        try:
            totals = await reports.daily(args.date)
            async with sessions() as session:
                pending = await session.scalar(
                    select(func.count()).select_from(Outbox).where(Outbox.delivered_at.is_(None))
                )
            print(
                json.dumps(
                    {
                        "day": args.date,
                        "timezone": "Europe/Moscow",
                        "totals": totals,
                        "pending_outbox": pending,
                        "provisional": bool(pending),
                    },
                    ensure_ascii=False,
                )
            )
            if args.command == "verify-report" and totals != await reports.daily(
                args.date, raw=True
            ):
                raise RuntimeError("Daily aggregates do not match immutable log")
        finally:
            await reports.close()
            await engine.dispose()
    elif args.command == "dlq":
        from delivery_service.broker import DLQ, Broker

        broker = Broker(settings)
        try:
            await broker.connect()
            if broker.channel is None:
                raise RuntimeError("Broker unavailable")
            queue = await broker.channel.declare_queue(DLQ, passive=True)
            print(json.dumps({"queue": DLQ, "messages": queue.declaration_result.message_count}))
        finally:
            await broker.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("worker")
    outbox = commands.add_parser("outbox")
    outbox.add_argument("--once", action="store_true")
    commands.add_parser("index-knowledge")
    commands.add_parser("dlq")
    retry = commands.add_parser("retry-parcel")
    retry.add_argument("id")
    for name in ("report-daily", "verify-report"):
        report = commands.add_parser(name)
        report.add_argument("--date", required=True)
    args = parser.parse_args()
    settings = Settings()
    configure(settings.log_level)
    asyncio.run(execute(args, settings))
