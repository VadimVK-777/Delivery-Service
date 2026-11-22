from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from delivery_service.broker import Broker


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
        assert channel.declare_queue.await_count == 2
        assert channel.default_exchange.publish.call_args.kwargs["mandatory"] is True
        channel.default_exchange.publish.return_value = False
        with pytest.raises(RuntimeError, match="not confirmed"):
            await broker.publish(b"{}", "event")
        await broker.close()
        connection.close.assert_awaited_once()
