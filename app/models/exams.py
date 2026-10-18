from datetime import date

from pydantic import BaseModel, Field, model_validator


class ExamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    class_id: int | None = None  # None = exam applies to ALL classes
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def check_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on/after start_date")
        return self


class ExamUpdate(BaseModel):
    name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    results_published: bool | None = None


class ResultEntry(BaseModel):
    student_id: int
    marks: float | None = None
    grade: str | None = Field(default=None, max_length=10)
    remarks: str | None = Field(default=None, max_length=255)


class ResultsSave(BaseModel):
    section_id: int
    results: list[ResultEntry]
