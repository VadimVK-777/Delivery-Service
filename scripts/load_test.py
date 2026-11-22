"""Explicit opt-in registration load against a disposable deployment."""

import argparse
import asyncio
import statistics
import time
from uuid import uuid4

import httpx


async def run(url: str, count: int, concurrency: int) -> None:
    latencies: list[float] = []
    failures = 0
    next_job = 0
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        response = await client.get("/api/v1/session")
        response.raise_for_status()
        csrf = response.json()["csrf_token"]

        async def producer() -> None:
            nonlocal next_job, failures
            while next_job < count:
                next_job += 1
                started = time.monotonic()
                try:
                    result = await client.post(
                        "/api/v1/parcels",
                        headers={
                            "X-CSRF-Token": csrf,
                            "Idempotency-Key": str(uuid4()),
                        },
                        json={
                            "name": "Load test",
                            "weight_kg": "1",
                            "type_id": next_job % 3 + 1,
                            "declared_value_usd": "10",
                        },
                    )
                    if result.status_code != 202:
                        failures += 1
                except httpx.HTTPError:
                    failures += 1
                latencies.append(time.monotonic() - started)

        started = time.monotonic()
        await asyncio.gather(*(producer() for _ in range(concurrency)))
    duration = time.monotonic() - started
    print(
        {
            "requests": count,
            "failures": failures,
            "seconds": duration,
            "requests_per_second": count / duration,
            "p95_seconds": statistics.quantiles(latencies, n=100)[94],
        }
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--concurrency", type=int, default=50)
    args = parser.parse_args()
    if args.count < 2 or args.concurrency < 1:
        parser.error("count >= 2 and concurrency >= 1 required")
    asyncio.run(run(args.url, args.count, args.concurrency))
