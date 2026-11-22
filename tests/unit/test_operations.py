import argparse
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from delivery_service.broker import Broker
from delivery_service.cli import execute, main


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
