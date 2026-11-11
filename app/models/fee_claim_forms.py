from datetime import date

from pydantic import BaseModel, Field

from app.models.common import PaymentMethod


class FeeClaimFormAccept(BaseModel):
    note: str | None = Field(default=None, max_length=255)
    # only for a form linked to an unpaid fee: also mark that fee paid (+ a transaction)
    mark_fee_paid: bool = True


class FeeClaimFormDecline(BaseModel):
    note: str | None = Field(default=None, max_length=255)


class FeeClaimFormEdit(BaseModel):
    """A reviewer corrects the parent's details; fields left out keep their value."""

    amount: float | None = Field(default=None, gt=0, le=10_000_000)
    paid_on: date | None = None
    method: PaymentMethod | None = None
    reference_no: str | None = Field(default=None, max_length=100)
    purpose: str | None = Field(default=None, max_length=150)
