from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["holiday", "exam", "event"]


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    category: Category = "event"


class EventUpdate(EventCreate):
    dates: list[date] = []
