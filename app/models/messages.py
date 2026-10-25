from pydantic import BaseModel, Field, model_validator


class MessageCreate(BaseModel):
    """A message to parents. Name exactly one target: `student_ids`,
    `section_id`, `class_id` or `whole_school`."""

    title: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=4000)
    student_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)
    section_id: int | None = None
    class_id: int | None = None
    whole_school: bool = False

    @model_validator(mode="after")
    def _one_target(self):
        targets = [self.student_ids is not None, self.section_id is not None, self.class_id is not None,
                   self.whole_school]
        if sum(targets) != 1:
            raise ValueError("Choose exactly one of: student_ids, section_id, class_id, whole_school")
        return self
