from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pereklichka.domain.family import MODERATION_CODE, LinkCode

NOW = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


def test_issued_code_is_six_digits_valid_for_fifteen_minutes() -> None:
    code = LinkCode.issue(ward_id=uuid4(), now=NOW)

    assert len(code.code) == 6
    assert code.code.isdigit()
    assert code.expires_at == NOW + timedelta(minutes=15)


def test_normal_generator_never_issues_moderation_identifier(monkeypatch) -> None:
    monkeypatch.setattr(
        "pereklichka.domain.family.secrets.randbelow", lambda _: int(MODERATION_CODE)
    )
    assert LinkCode.issue(uuid4(), NOW).code != MODERATION_CODE
