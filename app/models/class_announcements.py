from pydantic import BaseModel, Field


class ClassAnnouncementCreate(BaseModel):
    """A teacher's announcement to sections they teach (managers: any section).
    The PDF / photo goes up afterwards: POST /class-announcements/{id}/attachment."""

    title: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=4000)
    section_ids: list[int] = Field(min_length=1, max_length=100)


class ClassAnnouncementUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=4000)
