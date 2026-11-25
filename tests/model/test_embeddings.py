"""Opt-in real pinned model smoke test; no external LLM credentials required."""

import os
from typing import Any

import pytest

from delivery_service.rag import Support


async def test_pinned_model_indexes_and_retrieves(redis: Any, settings: Any) -> None:
    if os.getenv("RUN_MODEL_TEST") != "1":
        pytest.skip("Set RUN_MODEL_TEST=1 to download and verify the pinned embedding model")
    settings.qdrant_url = ":memory:"
    support = Support(settings, redis)
    try:
        version = await support.index()
        assert version == await support.index()
        embeddings = await support.load()
        for question, expected in [
            ("Можно ли отправить ноутбук?", "Ноутбук"),
            ("Какой максимальный вес посылки?", "30"),
        ]:
            result = await support.client.query_points(
                support.collection,
                query=embeddings.embed_query(question),
                limit=2,
            )
            assert any(expected in point.payload["page_content"] for point in result.points)
    finally:
        await support.close()
