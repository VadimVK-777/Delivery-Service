"""Public and broker contracts; monetary JSON values are decimal strings."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_WEIGHT_KG = 30
MAX_DECLARED_VALUE_USD = 10_000

Weight = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False)]
Money = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=2, allow_inf_nan=False)]


class ParcelTypeView(BaseModel):
    id: int
    name: str


class ParcelData(BaseModel):
    """Fields shared by stored parcels and existing version-1 broker messages."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)
    weight_kg: Weight
    type_id: int = Field(ge=1, le=3)
    declared_value_usd: Money


class ParcelInput(ParcelData):
    weight_kg: Weight = Field(le=MAX_WEIGHT_KG)
    declared_value_usd: Money = Field(le=MAX_DECLARED_VALUE_USD)


class Registration(BaseModel):
    id: UUID
    status: str
    status_url: str


class ParcelView(ParcelData):
    id: UUID
    type_name: str
    delivery_cost_rub: Decimal | None
    delivery_cost_display: str
    status: Literal["pending", "calculated", "failed"]
    carrier_id: int | None
    created_at: datetime


class ParcelPage(BaseModel):
    items: list[ParcelView]
    next_cursor: str | None


class ParcelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    id: UUID
    owner_id: UUID
    parcel: ParcelData
    created_at: datetime


class CarrierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: int = Field(gt=0, le=9223372036854775807)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)


class Answer(BaseModel):
    answer: str
    sources: list[str]
