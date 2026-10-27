from datetime import date

from pydantic import BaseModel, Field, model_validator

MARKS_LIMIT = 10000  # DECIMAL(6,2) holds up to 9999.99


class ExamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    class_id: int | None = None  # None = exam applies to ALL classes
    start_date: date
    end_date: date
    max_marks: float | None = Field(default=None, gt=0, lt=MARKS_LIMIT,
                                    description="Usual maximum per subject paper, e.g. 100")

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
    max_marks: float | None = Field(default=None, gt=0, lt=MARKS_LIMIT)


class ResultEntry(BaseModel):
    student_id: int
    marks: float | None = Field(default=None, ge=0, lt=MARKS_LIMIT, description="0 up to the maximum")
    grade: str | None = Field(default=None, max_length=10)
    remarks: str | None = Field(default=None, max_length=255)


class ResultsSave(BaseModel):
    section_id: int
    # leave out (or "") for the whole exam, as before; otherwise one of the class's subjects
    subject: str | None = Field(default=None, max_length=100)
    # maximum for these marks; leave out to use the exam's max_marks (no maximum if neither is set)
    max_marks: float | None = Field(default=None, gt=0, lt=MARKS_LIMIT)
    results: list[ResultEntry]
