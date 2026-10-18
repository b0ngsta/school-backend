from pydantic import BaseModel, Field


class SlotUpsert(BaseModel):
    teacher_id: int
    day: int = Field(ge=0, le=6)  # 0=Mon … 6=Sun
    period: int = Field(ge=1, le=12)
    class_id: int
    section_id: int
    subject: str | None = Field(default=None, max_length=100)
