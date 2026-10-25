from datetime import time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TripType = Literal["pickup", "drop"]


def _check_weekdays(value: list[int]) -> list[int]:
    if not value or any(d < 0 or d > 6 for d in value):
        raise ValueError("weekdays must be numbers 0 (Mon) … 6 (Sun), at least one")
    return sorted(set(value))


class ShiftCreate(BaseModel):
    """One trip of a vehicle, e.g. "Morning Shift 1", pickup, 07:00–08:30."""

    name: str = Field(min_length=1, max_length=50)
    trip_type: TripType
    start_time: time
    end_time: time
    weekdays: list[int] = Field(default=[0, 1, 2, 3, 4, 5], description="Mon=0 … Sat=5, Sunday=6")
    is_active: bool = True

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, value: list[int]) -> list[int]:
        return _check_weekdays(value)


class ShiftUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    trip_type: TripType | None = None
    start_time: time | None = None
    end_time: time | None = None
    weekdays: list[int] | None = None
    is_active: bool | None = None

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else _check_weekdays(value)


class ShiftStop(BaseModel):
    """A student on a shift. The list order is the stop order."""

    student_id: int
    stop_time: time | None = None
    stop_name: str | None = Field(default=None, max_length=150)
    landmark: str | None = Field(default=None, max_length=150)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, decimal_places=6)
    notes: str | None = Field(default=None, max_length=300)


class ShiftStudentsSave(BaseModel):
    students: list[ShiftStop] = Field(max_length=200)
