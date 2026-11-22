"""Operational commands; financial reports are intentionally not public HTTP routes."""

import argparse
import asyncio
import json
from uuid import UUID

from delivery_service.settings import Settings


async def execute(args: argparse.Namespace, settings: Settings) -> None:
    if args.command == "worker":
        from delivery_service.worker import run

        await run(settings)
    elif args.command == "retry-parcel":
        from delivery_service.worker import retry_parcel

        await retry_parcel(UUID(args.id), settings)
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
    commands.add_parser("dlq")
    retry = commands.add_parser("retry-parcel")
    retry.add_argument("id")
    args = parser.parse_args()
    settings = Settings()
    asyncio.run(execute(args, settings))
