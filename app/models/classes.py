from pydantic import BaseModel, Field


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    fee_amount: float | None = Field(default=None, ge=0)
    subjects: list[str] = Field(default_factory=list)


class SubjectsUpdate(BaseModel):
    subjects: list[str] = Field(default_factory=list)


class SectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=10)
