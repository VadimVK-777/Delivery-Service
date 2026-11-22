from decimal import Decimal
from typing import Any

import httpx
import pytest

from delivery_service.domain import Problem
from delivery_service.rates import Rates


async def test_rate_cache_and_nominal(redis: Any, settings: Any) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json={"Date": "2026-01-01", "Valute": {"USD": {"Value": 180, "Nominal": 2}}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rates = Rates(redis, http, settings)
        assert await rates.get() == (Decimal("90"), "2026-01-01")
        assert await rates.get() == (Decimal("90"), "2026-01-01")
        assert len(calls) == 1
        assert await redis.ttl("exchange:usd") > 0


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"Date": "x", "Valute": {"USD": {"Value": -1, "Nominal": 1}}}),
    ],
)
async def test_no_stale_rate(redis: Any, settings: Any, response: httpx.Response) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        with pytest.raises(Problem):
            await Rates(redis, http, settings).get()
        assert await redis.get("exchange:lock") is None
        assert await redis.get("exchange:usd") is None
