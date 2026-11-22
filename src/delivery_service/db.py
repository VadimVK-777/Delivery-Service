"""PostgreSQL persistence; transaction boundaries belong to application services."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ParcelType(Base):
    __tablename__ = "parcel_types"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(40), unique=True)


class Parcel(Base):
    __tablename__ = "parcels"
    __table_args__ = (
        CheckConstraint("weight_kg > 0 AND declared_value_usd >= 0"),
        CheckConstraint("carrier_id IS NULL OR carrier_id > 0"),
        CheckConstraint("status IN ('pending', 'calculated', 'failed')"),
        Index("ix_parcels_owner_created", "owner_id", "created_at", "id"),
        Index("ix_parcels_owner_type_status", "owner_id", "type_id", "status", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(255))
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    type_id: Mapped[int] = mapped_column(ForeignKey("parcel_types.id"))
    declared_value_usd: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    delivery_cost_rub: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    rate_date: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    carrier_id: Mapped[int | None] = mapped_column(BigInteger)
    assignment_token_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Outbox(Base):
    __tablename__ = "calculation_outbox"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    parcel_id: Mapped[UUID] = mapped_column(ForeignKey("parcels.id"), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


def database(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
