from datetime import time
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.validators import clean_email
from app.models.common import PASSWORD_MAX_LENGTH

VehicleType = Literal["bus", "van", "other"]


class NewDriver(BaseModel):
    """A driver sign-in made together with the vehicle (Add / Edit vehicle form).
    The account's name and phone are the vehicle's driver_name / driver_phone."""

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)
    email: str | None = None

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        return clean_email(value)


class VehicleCreate(BaseModel):
    vehicle_no: str = Field(min_length=1, max_length=30)
    driver_name: str = Field(min_length=1, max_length=100)
    driver_phone: str | None = None
    route_name: str | None = None
    capacity: int | None = Field(default=None, gt=0)
    status: Literal["active", "maintenance"] = "active"
    vehicle_type: VehicleType = "bus"
    driver_id: int | None = Field(default=None, description="The driver's login account (for the photo)")
    shift_name: str | None = Field(default=None, max_length=50)
    shift_start: time | None = None
    shift_end: time | None = None
    new_driver: NewDriver | None = Field(
        default=None, description="Create a driver account and link it (instead of driver_id)")


class VehicleUpdate(BaseModel):
    vehicle_no: str | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    route_name: str | None = None
    capacity: int | None = None
    status: Literal["active", "maintenance"] | None = None
    vehicle_type: VehicleType | None = None
    driver_id: int | None = None
    shift_name: str | None = Field(default=None, max_length=50)
    shift_start: time | None = None
    shift_end: time | None = None
    new_driver: NewDriver | None = Field(
        default=None, description="Create a driver account and link it (instead of driver_id)")


class StudentTransportSave(BaseModel):
    """A student's seat on a vehicle: the stop, pickup and drop times."""

    stop_name: str | None = Field(default=None, max_length=150)
    pickup_time: time | None = None
    drop_time: time | None = None
