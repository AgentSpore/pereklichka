from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pereklichka.domain.family import LinkCode

NOW = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


def test_issued_code_is_six_digits_valid_for_fifteen_minutes() -> None:
    code = LinkCode.issue(ward_id=uuid4(), now=NOW)

    assert len(code.code) == 6
    assert code.code.isdigit()
    assert code.expires_at == NOW + timedelta(minutes=15)


def test_code_is_active_until_expiry_and_only_once() -> None:
    code = LinkCode.issue(ward_id=uuid4(), now=NOW)

    assert code.is_active(NOW + timedelta(minutes=14))
    assert not code.is_active(NOW + timedelta(minutes=15))
    assert not replace(code, used_at=NOW).is_active(NOW)
