from pydantic import BaseModel, Field


class SmsCreate(BaseModel):
    # 'all_parents' | 'all_teachers' | 'all_staff' | 'section:<id>'
    recipient_group: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=1000)
    template: str | None = None
