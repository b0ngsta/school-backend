from typing import Literal

from pydantic import BaseModel, Field


class VehicleCreate(BaseModel):
    vehicle_no: str = Field(min_length=1, max_length=30)
    driver_name: str = Field(min_length=1, max_length=100)
    driver_phone: str | None = None
    route_name: str | None = None
    capacity: int | None = Field(default=None, gt=0)
    status: Literal["active", "maintenance"] = "active"


class VehicleUpdate(BaseModel):
    vehicle_no: str | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    route_name: str | None = None
    capacity: int | None = None
    status: Literal["active", "maintenance"] | None = None
