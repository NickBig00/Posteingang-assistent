"""Loading the fictitious member and contract data into the database."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.models import Member

DEFAULT_MEMBERS_FILE = Path(__file__).resolve().parent.parent / "samples/fitness/members.json"


class MemberSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_number: str = Field(pattern=r"^M-\d{5}$")
    name: str
    email: str
    contract_start: date
    minimum_term_months: int = Field(ge=1)
    notice_period_months: int = Field(ge=0)
    renewal_period_months: int = Field(ge=1)
    monthly_fee_eur: Decimal = Field(gt=0, decimal_places=2)


class MembersFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    synthetic: bool
    members: list[MemberSeed]


def load_member_seeds(path: Path = DEFAULT_MEMBERS_FILE) -> list[MemberSeed]:
    """Read and validate the member seed file. Only files marked synthetic are accepted."""
    document = MembersFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if not document.synthetic:
        raise ValueError(f"{path} is not marked as synthetic data")
    return document.members


def seed_members(session: Session, path: Path = DEFAULT_MEMBERS_FILE) -> int:
    """Insert all members from `path` that do not exist yet. Returns the number inserted."""
    existing = set(session.exec(select(Member.member_number)).all())
    inserted = 0
    for seed in load_member_seeds(path):
        if seed.member_number in existing:
            continue
        session.add(Member(**seed.model_dump()))
        inserted += 1
    session.commit()
    return inserted
