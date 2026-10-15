import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.models import Member
from app.seed import load_member_seeds, seed_members


def test_load_member_seeds_reads_sample_file() -> None:
    seeds = load_member_seeds()

    assert len(seeds) == 10
    assert all(seed.email.endswith(("example.com", "example.org", "example.net")) for seed in seeds)


def test_seed_members_inserts_typed_contract_data(session: Session) -> None:
    inserted = seed_members(session)

    member = session.exec(select(Member).where(Member.member_number == "M-10004")).one()
    assert inserted == 10
    assert member.contract_start == date(2023, 6, 1)
    assert member.notice_period_months == 3
    assert member.monthly_fee_eur == Decimal("49.90")


def test_seed_members_is_idempotent(session: Session) -> None:
    seed_members(session)

    assert seed_members(session) == 0
    assert len(session.exec(select(Member)).all()) == 10


def test_non_synthetic_seed_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "members.json"
    path.write_text(json.dumps({"synthetic": False, "members": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="synthetic"):
        load_member_seeds(path)


def test_invalid_member_number_is_rejected(tmp_path: Path) -> None:
    member = {
        "member_number": "12345",
        "name": "Fiktiv",
        "email": "fiktiv@example.com",
        "contract_start": "2026-01-01",
        "minimum_term_months": 12,
        "notice_period_months": 1,
        "renewal_period_months": 1,
        "monthly_fee_eur": "29.90",
    }
    path = tmp_path / "members.json"
    path.write_text(json.dumps({"synthetic": True, "members": [member]}), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_member_seeds(path)
