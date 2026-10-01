from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pereklichka.domain.family import LinkCode

NOW = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


def test_issued_code_is_six_digits_valid_for_fifteen_minutes() -> None:
    code = LinkCode.issue(ward_id=uuid4(), now=NOW)

    assert len(code.code) == 6
    assert code.code.isdigit()
    assert code.expires_at == NOW + timedelta(minutes=15)
