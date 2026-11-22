"""Versioned local embeddings, Qdrant retrieval and grounded LangChain generation."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from fastembed import TextEmbedding
from huggingface_hub import snapshot_download
from langchain_core.embeddings import Embeddings
from qdrant_client import QdrantClient, models
from redis.asyncio import Redis
from tokenizers import Tokenizer

from delivery_service.settings import Settings


class TokenizedModel(Protocol):
    tokenizer: Tokenizer


class LocalEmbeddings(Embeddings):
    def __init__(self, name: str, revision: str) -> None:
        descriptor = next(m for m in TextEmbedding.list_supported_models() if m["model"] == name)
        model_path = snapshot_download(
            repo_id=descriptor["sources"]["hf"],
            revision=revision,
            allow_patterns=["*.json", "*.txt", "*.onnx", "*.onnx_data"],
        )
        self.model = TextEmbedding(model_name=name, specific_model_path=model_path, threads=2)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [float(value) for value in next(iter(self.model.query_embed(text)))]


class Support:
    def __init__(self, settings: Settings, redis: Redis) -> None:
        self.settings, self.redis = settings, redis
        self.embeddings: LocalEmbeddings | None = None
        self.client = QdrantClient(url=settings.qdrant_url, timeout=10, check_compatibility=False)
        self.semaphore = asyncio.Semaphore(2)
        self.load_lock = asyncio.Lock()
        self.collection = "delivery_rules"

    async def load(self) -> LocalEmbeddings:
        async with self.load_lock:
            if self.embeddings is None:
                self.embeddings = await asyncio.to_thread(
                    LocalEmbeddings, self.settings.embedding_model, self.settings.embedding_revision
                )
            return self.embeddings

    async def index(self) -> str:
        """Build a content-addressed collection and atomically switch the read alias."""
        text = Path(self.settings.knowledge_file).read_text(encoding="utf-8")
        version = hashlib.sha256(
            (self.settings.embedding_model + self.settings.embedding_revision + text).encode()
        ).hexdigest()[:16]
        name = f"delivery_rules_{version}"
        embeddings = await self.load()

        def build() -> None:
            if not self.client.collection_exists(name):
                self.client.create_collection(
                    name,
                    vectors_config=models.VectorParams(
                        size=len(embeddings.embed_query("dimension")),
                        distance=models.Distance.COSINE,
                    ),
                )
            # FastEmbed's tokenizer keeps each chunk below the model token window.
            tokenizer = cast(TokenizedModel, embeddings.model.model).tokenizer
            truncation = tokenizer.truncation
            window = min(400, truncation["max_length"] - 2) if truncation else 400
            tokenizer.no_truncation()
            chunks: list[str] = []
            for section in text.split("\n## "):
                tokens = tokenizer.encode(section, add_special_tokens=False).ids
                for start in range(0, len(tokens), window - min(50, window // 2)):
                    chunks.append(tokenizer.decode(tokens[start : start + window]))
            if truncation:
                tokenizer.enable_truncation(**truncation)
            vectors = embeddings.embed_documents(chunks)
            self.client.upsert(
                name,
                points=[
                    models.PointStruct(
                        id=str(uuid5(NAMESPACE_URL, f"{version}:{index}")),
                        vector=vector,
                        payload={
                            "page_content": chunk,
                            "metadata": {
                                "source": f"rules.md#{index + 1}",
                                "version": version,
                            },
                        },
                    )
                    for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
                ],
            )
            aliases = self.client.get_aliases().aliases
            actions: list[Any] = []
            if any(alias.alias_name == self.collection for alias in aliases):
                actions.append(
                    models.DeleteAliasOperation(
                        delete_alias=models.DeleteAlias(alias_name=self.collection)
                    )
                )
            actions.append(
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name=name, alias_name=self.collection
                    )
                )
            )
            self.client.update_collection_aliases(actions)

        await asyncio.to_thread(build)
        return version

    async def close(self) -> None:
        await asyncio.to_thread(self.client.close)
