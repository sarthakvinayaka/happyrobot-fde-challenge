from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LoadBase(BaseModel):
    load_id: str = Field(..., max_length=64)
    origin: str
    destination: str
    pickup_datetime: str
    delivery_datetime: str
    equipment_type: str
    loadboard_rate: float
    notes: str
    weight: int = Field(..., ge=0)
    commodity_type: str
    num_of_pieces: int = Field(..., ge=0)
    miles: int = Field(..., ge=0)
    dimensions: str


class LoadCreate(LoadBase):
    pass


class LoadRead(LoadBase):
    model_config = ConfigDict(from_attributes=True)


class CarrierVerifyResponse(BaseModel):
    """Unified shape: either valid + details, or valid + reason."""

    valid: bool
    details: dict[str, Any] | None = None
    reason: str | None = None
