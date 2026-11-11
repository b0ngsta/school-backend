from pydantic import BaseModel, Field


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    fee_amount: float | None = Field(default=None, ge=0)
    subjects: list[str] = Field(default_factory=list)
    # how many sections to create with the class: 4 = A, B, C, D · 0 = the class is not split
    # into sections (it works as one group) · left out (older apps) = none yet, add them later
    sections: int | None = Field(default=None, ge=0, le=26)


class ClassUpdate(BaseModel):
    """PUT /classes/{id}: fields left out keep their value (fee_amount: null = no fee set)."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    fee_amount: float | None = Field(default=None, ge=0, le=10_000_000)


class SubjectsUpdate(BaseModel):
    subjects: list[str] = Field(default_factory=list)


class SectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=10)
