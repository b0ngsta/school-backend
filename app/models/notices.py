from pydantic import BaseModel, Field


class NoticeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str
