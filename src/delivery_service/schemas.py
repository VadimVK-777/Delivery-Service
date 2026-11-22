"""Public and broker contracts; monetary JSON values are decimal strings."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Weight = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False)]
Money = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=2, allow_inf_nan=False)]


class ParcelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)
    weight_kg: Weight
    type_id: int = Field(ge=1, le=3)
    declared_value_usd: Money


class Registration(BaseModel):
    id: UUID
    status: str
    status_url: str


class ParcelView(ParcelInput):
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
    parcel: ParcelInput
    created_at: datetime


class CarrierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: int = Field(gt=0, le=9223372036854775807)
