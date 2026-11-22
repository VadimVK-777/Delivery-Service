"""Operational commands; financial reports are intentionally not public HTTP routes."""

import argparse
import asyncio

from delivery_service.settings import Settings


async def execute(args: argparse.Namespace, settings: Settings) -> None:
    if args.command == "worker":
        from delivery_service.worker import run

        await run(settings)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("worker")
    args = parser.parse_args()
    settings = Settings()
    asyncio.run(execute(args, settings))
