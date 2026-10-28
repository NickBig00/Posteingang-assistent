"""Fictitious member list shared by all modules.

The inbox checks contract data against it; the social media module makes sure that no
member is named in a post.
"""

from datetime import date
from decimal import Decimal

from sqlmodel import Field, SQLModel


class Member(SQLModel, table=True):
    """Fictitious member with contract data (demo table the rules check against)."""

    id: int | None = Field(default=None, primary_key=True)
    member_number: str = Field(unique=True, index=True)
    name: str
    email: str
    contract_start: date
    minimum_term_months: int = Field(ge=1)
    notice_period_months: int = Field(ge=0)
    renewal_period_months: int = Field(ge=1)
    monthly_fee_eur: Decimal = Field(max_digits=8, decimal_places=2)
