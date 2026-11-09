from pydantic import BaseModel, Field


class FeeClaimFormAccept(BaseModel):
    note: str | None = Field(default=None, max_length=255)
    # only for a form linked to an unpaid fee: also mark that fee paid (+ a transaction)
    mark_fee_paid: bool = True


class FeeClaimFormDecline(BaseModel):
    note: str | None = Field(default=None, max_length=255)
