import argparse
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from delivery_service.broker import Broker
from delivery_service.cli import execute, main
from delivery_service.db import Outbox
from delivery_service.reporting import run as outbox_run


async def test_broker_publisher_confirm_and_queues(settings: Any) -> None:
    connection = AsyncMock()
    connection.is_closed = False
    channel = AsyncMock()
    channel.is_closed = False
    connection.channel.return_value = channel
    with patch(
        "delivery_service.broker.aio_pika.connect_robust", return_value=connection
    ) as connect:
        broker = Broker(settings)
        await broker.publish(b"{}", "event")
        await broker.publish(b"{}", "event")
        connect.assert_awaited_once()
        assert channel.declare_queue.await_count == 7
        assert channel.default_exchange.publish.call_args.kwargs["mandatory"] is True
        channel.default_exchange.publish.return_value = False
        with pytest.raises(RuntimeError, match="not confirmed"):
            await broker.publish(b"{}", "event")
        await broker.close()
        connection.close.assert_awaited_once()


async def test_outbox_marks_delivered_only_after_mongo(db: Any, settings: Any) -> None:
    # SQLite fixture tests the transaction boundary; real Mongo is covered in integration tests.
    event_id = uuid4()
    async with db.begin() as session:
        session.add(Outbox(id=event_id, parcel_id=uuid4(), payload={"event_id": str(event_id)}))
    engine = AsyncMock()
    reports = AsyncMock()
    reports.record.side_effect = OSError("Mongo offline")
    with (
        patch("delivery_service.reporting.database", return_value=(engine, db)),
        patch("delivery_service.reporting.Reports", return_value=reports),
    ):
        with pytest.raises(OSError):
            await outbox_run(settings, once=True)
        async with db() as session:
            assert (await session.get(Outbox, event_id)).delivered_at is None
        reports.record.side_effect = None
        await outbox_run(settings, once=True)
        async with db() as session:
            assert (await session.get(Outbox, event_id)).delivered_at is not None
        await outbox_run(settings, once=True)
        assert reports.record.await_count == 2


@pytest.mark.parametrize(
    ("command", "target", "extra"),
    [
        ("worker", "delivery_service.worker.run", {}),
        ("outbox", "delivery_service.reporting.run", {"once": True}),
        ("retry-parcel", "delivery_service.worker.retry_parcel", {"id": str(uuid4())}),
    ],
)
async def test_cli_operational_dispatch(
    settings: Any, command: str, target: str, extra: Any
) -> None:
    with patch(target, new_callable=AsyncMock) as operation:
        await execute(argparse.Namespace(command=command, **extra), settings)
        operation.assert_awaited_once()


async def test_report_cli_and_discrepancy(db: Any, settings: Any, capsys: Any) -> None:
    reports = AsyncMock()
    reports.daily.return_value = [{"_id": 1, "amount_kopecks": 20, "count": 1}]
    with (
        patch("delivery_service.reporting.Reports", return_value=reports),
        patch("delivery_service.cli.database", return_value=(AsyncMock(), db)),
    ):
        await execute(argparse.Namespace(command="report-daily", date="2026-01-01"), settings)
        assert '"provisional": false' in capsys.readouterr().out
        reports.daily.side_effect = [[{"_id": 1}], []]
        with pytest.raises(RuntimeError, match="do not match"):
            await execute(argparse.Namespace(command="verify-report", date="2026-01-01"), settings)


async def test_index_cli_closes_resources(settings: Any) -> None:
    support = AsyncMock()
    with (
        patch("delivery_service.rag.Support", return_value=support),
        patch("delivery_service.cli.Redis.from_url", return_value=AsyncMock()),
    ):
        await execute(argparse.Namespace(command="index-knowledge"), settings)
        support.index.assert_awaited_once()
        support.close.assert_awaited_once()


async def test_dlq_cli(settings: Any, capsys: Any) -> None:
    broker = AsyncMock()
    broker.channel.declare_queue.return_value = SimpleNamespace(
        declaration_result=SimpleNamespace(message_count=4)
    )
    with patch("delivery_service.broker.Broker", return_value=broker):
        await execute(argparse.Namespace(command="dlq"), settings)
        assert '"messages": 4' in capsys.readouterr().out


def test_cli_entrypoint(settings: Any) -> None:
    with (
        patch("sys.argv", ["delivery", "dlq"]),
        patch("delivery_service.cli.Settings", return_value=settings),
        patch("delivery_service.cli.asyncio.run", side_effect=lambda coro: coro.close()) as run,
    ):
        main()
        run.assert_called_once()
