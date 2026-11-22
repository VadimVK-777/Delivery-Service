"""Validated environment configuration. Secrets never appear in model reprs."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", validate_assignment=True)

    database_url: str = "postgresql+asyncpg://delivery:delivery@localhost:5432/delivery"
    redis_url: str = "redis://localhost:6379/0"
    rabbitmq_url: SecretStr = SecretStr("amqp://delivery:delivery@localhost:5672/")
    mongo_url: SecretStr = SecretStr("mongodb://localhost:27017/?replicaSet=rs0")
    qdrant_url: str = "http://localhost:6333"
    session_secret: SecretStr = Field(min_length=32)
    cookie_secure: bool = False
    session_ttl: int = 30 * 24 * 3600
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "openai/gpt-oss-20b"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_revision: str = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
    knowledge_file: str = "knowledge/rules.md"
    cbr_url: str = "https://www.cbr-xml-daily.ru/daily_json.js"
    rate_ttl: int = 3600
    worker_concurrency: int = Field(default=16, ge=1, le=128)
    log_level: str = "INFO"
