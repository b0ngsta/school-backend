"""Report-card designs and print runs saved for later (migration 0025)."""
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

TemplateKind = Literal["report_card", "report_card_back"]

MAX_ELEMENTS = 400


class PrintTemplateSave(BaseModel):
    """Create / replace a design. `layout` is the web designer's JSON (page +
    positioned elements); the server stores it as given after a shape check."""

    name: str = Field(min_length=1, max_length=80)
    kind: TemplateKind = "report_card"
    is_default: bool = False
    layout: dict[str, Any]

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("layout")
    @classmethod
    def _layout(cls, v: dict) -> dict:
        elements = v.get("elements")
        if not isinstance(elements, list):
            raise ValueError("layout.elements must be a list")
        if len(elements) > MAX_ELEMENTS:
            raise ValueError(f"a design can hold at most {MAX_ELEMENTS} elements")
        if not all(isinstance(e, dict) for e in elements):
            raise ValueError("every element must be an object")
        if not isinstance(v.get("page", {}), dict):
            raise ValueError("layout.page must be an object")
        return v


class PendingStudent(BaseModel):
    """A student whose report card is waiting: some marks were neither entered nor marked absent."""

    student_id: int
    section_id: int | None = None
    full_name: str | None = Field(default=None, max_length=150)
    missing: list[str] = Field(default_factory=list, max_length=60)


class PrintBatchCreate(BaseModel):
    source: Literal["exam", "final"]
    exam_id: int | None = None
    class_id: int | None = None
    front_template_id: int | None = None
    back_template_id: int | None = None
    title: str = Field(min_length=1, max_length=160)
    pending: list[PendingStudent] = Field(min_length=1, max_length=1000)
    printed_count: int = Field(default=0, ge=0, le=100000)


class PrintBatchUpdate(BaseModel):
    """What is still waiting after a later print; an empty list closes the batch."""

    pending: list[PendingStudent] | None = Field(default=None, max_length=1000)
    printed_count: int | None = Field(default=None, ge=0, le=100000)
    status: Literal["open", "done"] | None = None
