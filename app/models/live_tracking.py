from datetime import datetime

from pydantic import BaseModel, Field

MAX_POINTS = 20


class LocationPoint(BaseModel):
    """One GPS fix from the driver's phone."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    recorded_at: datetime = Field(description="When the phone took the fix: ISO 8601 or Unix milliseconds / seconds")
    speed: float | None = Field(default=None, ge=0, le=100, description="metres per second")
    heading: float | None = Field(default=None, ge=0, le=360, description="degrees, 0 = north")
    accuracy: float | None = Field(default=None, ge=0, le=100000, description="metres")


class LocationBatch(BaseModel):
    """POST /driver/location: the fixes taken since the last upload, oldest first."""

    points: list[LocationPoint] = Field(min_length=1, max_length=MAX_POINTS)
    vehicle_id: int | None = Field(default=None, description="Only for a driver with more than one vehicle")
