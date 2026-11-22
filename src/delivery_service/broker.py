"""Confirmed publications and bounded retry queues."""

import asyncio
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractQueue, AbstractRobustConnection

from delivery_service.settings import Settings

QUEUE = "parcels.register"
DLQ = "parcels.failed"
DELAYS = (10, 60, 300, 1800, 7200)


class Broker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.connection: AbstractRobustConnection | None = None
        self.channel: AbstractChannel | None = None
        self.queue: AbstractQueue | None = None
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        async with self.lock:
            if (
                self.connection
                and not self.connection.is_closed
                and self.channel
                and not self.channel.is_closed
                and self.queue
            ):
                return
            if self.connection and not self.connection.is_closed:
                await self.connection.close()
            self.connection = await aio_pika.connect_robust(
                self.settings.rabbitmq_url.get_secret_value(), timeout=5
            )
            self.channel = await self.connection.channel(
                publisher_confirms=True, on_return_raises=True
            )
            await self.channel.set_qos(prefetch_count=self.settings.worker_concurrency)
            await self.channel.declare_queue(
                DLQ, durable=True, arguments={"x-queue-type": "quorum"}
            )
            self.queue = await self.channel.declare_queue(
                QUEUE,
                durable=True,
                arguments={
                    "x-queue-type": "quorum",
                    "x-dead-letter-exchange": "",
                    "x-dead-letter-routing-key": DLQ,
                    "x-overflow": "reject-publish",
                    "x-dead-letter-strategy": "at-least-once",
                },
            )
            for delay in DELAYS:
                await self.channel.declare_queue(
                    f"{QUEUE}.retry.{delay}",
                    durable=True,
                    arguments={
                        "x-queue-type": "quorum",
                        "x-message-ttl": delay * 1000,
                        "x-dead-letter-exchange": "",
                        "x-dead-letter-routing-key": QUEUE,
                        "x-overflow": "reject-publish",
                        "x-dead-letter-strategy": "at-least-once",
                    },
                )

    async def publish(
        self, body: bytes, message_id: str, *, queue: str = QUEUE, attempt: int = 0
    ) -> None:
        await self.connect()
        if self.channel is None:
            raise RuntimeError("Broker channel unavailable")
        result: Any = await self.channel.default_exchange.publish(
            aio_pika.Message(
                body,
                content_type="application/json",
                message_id=message_id,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers={"attempt": attempt},
            ),
            routing_key=queue,
            mandatory=True,
            timeout=5,
        )
        if result is False:
            raise RuntimeError("Publication was not confirmed")

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()
