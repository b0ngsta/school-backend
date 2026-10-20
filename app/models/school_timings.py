import re

from pydantic import BaseModel, Field, field_validator, model_validator

_HM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MINUTES_PER_DAY = 24 * 60


class Break(BaseModel):
    after_period: int
    minutes: int = Field(ge=1, le=180)
    label: str | None = Field(default=None, max_length=60)


class SchoolTimings(BaseModel):
    """PUT /settings/school-timings. `periods`, `updated_at` and
    `updated_by_name` in the body (as returned by GET) are ignored."""

    start_time: str = Field(description="HH:MM, 24h school time")
    prayer_minutes: int = Field(default=0, ge=0, le=120)
    period_minutes: int = Field(ge=10, le=180)
    periods_per_day: int = Field(default=8, ge=1, le=12)
    breaks: list[Break] = []

    @field_validator("start_time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        value = (value or "").strip()
        if len(value) == 4 and value[1] == ":":  # "6:30"
            value = "0" + value
        if len(value) == 8 and value[5] == ":":  # "06:30:00"
            value = value[:5]
        if not _HM.match(value):
            raise ValueError("start_time must be HH:MM")
        return value

    @model_validator(mode="after")
    def _check_day(self):
        seen = set()
        for b in self.breaks:
            if not 1 <= b.after_period <= self.periods_per_day - 1:
                raise ValueError(f"A break must come after period 1 … {self.periods_per_day - 1}")
            if b.after_period in seen:
                raise ValueError(f"Only one break after period {b.after_period}")
            seen.add(b.after_period)
        hours, minutes = map(int, self.start_time.split(":"))
        end = (hours * 60 + minutes + self.prayer_minutes + self.period_minutes * self.periods_per_day
               + sum(b.minutes for b in self.breaks))
        if end >= MINUTES_PER_DAY:
            raise ValueError("The school day must end before midnight")
        return self
