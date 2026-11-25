"""Shared currency cache with bounded refresh locking and no stale fallback."""

import asyncio
import json
import secrets
from collections.abc import Awaitable
from decimal import Decimal
from typing import Any, cast

import httpx
from redis.asyncio import Redis

from delivery_service.domain import Problem
from delivery_service.settings import Settings

UNLOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end"


class Rates:
    def __init__(self, redis: Redis, http: httpx.AsyncClient, settings: Settings) -> None:
        self.redis, self.http, self.settings = redis, http, settings

    async def get(self) -> tuple[Decimal, str]:
        for _ in range(30):
            cached = await self.redis.get("exchange:usd")
            if cached:
                data = json.loads(cached)
                return Decimal(data["rate"]), data["date"]
            token = secrets.token_hex(16)
            if await self.redis.set("exchange:lock", token, nx=True, ex=20):
                try:
                    response = await self.http.get(self.settings.cbr_url, timeout=5)
                    response.raise_for_status()
                    data = json.loads(response.text, parse_float=Decimal)
                    usd = data["Valute"]["USD"]
                    rate = Decimal(str(usd["Value"])) / Decimal(str(usd["Nominal"]))
                    date = str(data["Date"])
                    if not rate.is_finite() or rate <= 0:
                        raise ValueError("Non-positive rate")
                    payload: dict[str, Any] = {"rate": str(rate), "date": date}
                    await self.redis.set(
                        "exchange:usd", json.dumps(payload), ex=self.settings.rate_ttl
                    )
                    return rate, date
                except (httpx.HTTPError, ValueError, KeyError, ArithmeticError) as exc:
                    raise Problem(
                        503, "rate_unavailable", "Курс недоступен; расчёт отложен"
                    ) from exc
                finally:
                    await cast(Awaitable[Any], self.redis.eval(UNLOCK, 1, "exchange:lock", token))
            await asyncio.sleep(0.2)
        raise Problem(503, "rate_refresh_busy", "Обновление курса; повторите позже")
