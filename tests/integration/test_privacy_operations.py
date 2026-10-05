import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from pereklichka.db.models import (
    CheckInRow,
    FamilyRow,
    InvitationRow,
    LinkAttemptRow,
    LinkCodeRow,
    OutboxRow,
    RelativeRow,
    SilenceAlertRow,
    WardRow,
)

TABLES = (
    FamilyRow,
    WardRow,
    RelativeRow,
    InvitationRow,
    LinkCodeRow,
    CheckInRow,
    SilenceAlertRow,
    LinkAttemptRow,
    OutboxRow,
)
ROOT = Path(__file__).resolve().parents[2]


def family_records(family, ward, i, now):
    records = []
    records.append(RelativeRow(id=uuid4(), family_id=family, chat_id=101 + i, alert_order=0))
    records.append(
        InvitationRow(token_hash=str(i) * 64, family_id=family, expires_at=now + timedelta(days=1))
    )
    records.append(SilenceAlertRow(ward_id=ward, local_day=now.date(), first_at=now))
    records.append(LinkAttemptRow(application_id=f"device-{i}", at=now))
    for used in (False, True):
        records.append(
            LinkCodeRow(
                id=uuid4(),
                ward_id=ward,
                code=f"{i}{int(used)}0000",
                expires_at=now,
                used_at=now if used else None,
            )
        )
    checks = [uuid4(), uuid4()]
    for j, check in enumerate(checks):
        records.append(
            CheckInRow(
                id=check,
                ward_id=ward,
                at=now,
                wellbeing="Synthetic",
                completed_at=now if j == 0 else None,
            )
        )
    events = [
        (f"checkin:{checks[0]}", 101 + i, now),
        (f"checkin:{checks[0]}", 999 + i, None),
        (f"checkin:{checks[1]}", 101 + i, None),
        (f"silence:{ward}:pending", 101 + i, None),
        (f"silence:{ward}:delivered", 101 + i, now),
        (f"legacy:{i}", 101 + i, None),
    ]
    records.extend(
        OutboxRow(
            id=uuid4(),
            event_key=key,
            chat_id=chat,
            text="Synthetic",
            delivered_at=delivered,
        )
        for key, chat, delivered in events
    )
    return records


@pytest.fixture
async def seeded(sessionmaker):
    now = datetime.now(UTC)
    families, wards = [uuid4(), uuid4()], [uuid4(), uuid4()]
    async with sessionmaker() as session:
        session.add_all(FamilyRow(id=f) for f in families)
        await session.flush()
        for i, (family, ward) in enumerate(zip(families, wards, strict=True)):
            session.add(
                WardRow(
                    id=ward,
                    family_id=family,
                    name="Synthetic",
                    checkin_hour=9,
                    timezone="Europe/Moscow",
                    device_id=f"device-{i}",
                    consent_at=now,
                    consent_version="test",
                )
            )
        await session.flush()
        for i, (family, ward) in enumerate(zip(families, wards, strict=True)):
            session.add_all(family_records(family, ward, i, now))
        session.add(LinkAttemptRow(application_id="unattributed", at=now))
        await session.commit()
    return families, wards


@pytest.fixture
def execute_operation(database_url):
    executable = shutil.which("psql")
    if not executable:
        raise RuntimeError("Privacy operation tests require psql on PATH")
    url = make_url(database_url)
    if not all((url.host, url.username, url.password, url.database)):
        raise RuntimeError("Local test database URL lacks required components")
    env = os.environ.copy()
    env.update(
        PGHOST=str(url.host),
        PGPORT=str(url.port),
        PGUSER=str(url.username),
        PGPASSWORD=str(url.password),
        PGDATABASE=str(url.database),
    )

    def execute(script, target):
        variable = "ward_id" if script == "revoke-consent" else "family_id"
        variables = [] if target is None else ["-v", f"{variable}={target}"]
        result = subprocess.run(
            [
                executable,
                "-X",
                "-A",
                "-t",
                "--no-password",
                *variables,
                "-f",
                str(ROOT / "deploy" / f"{script}.sql"),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert re.fullmatch(
            r"(?:\d+\n|Invalid target UUID; rolled back\.\n|"
            r"Target not found; rolled back\.\n)*",
            result.stdout,
        )
        return result.returncode

    return execute


@pytest.fixture
def snapshot(sessionmaker):
    async def counts():
        async with sessionmaker() as session:
            return tuple(
                [await session.scalar(select(func.count()).select_from(table)) for table in TABLES]
            )

    return counts


async def test_revoke_preserves_history_and_foreign_family(
    seeded, sessionmaker, execute_operation, snapshot
):
    _, wards = seeded
    assert execute_operation("revoke-consent", wards[0]) == 0
    async with sessionmaker() as session:
        ward = await session.get(WardRow, wards[0])
        assert (ward.device_id, ward.consent_at, ward.consent_version) == (None, None, None)
        foreign = await session.get(WardRow, wards[1])
        assert foreign.device_id == "device-1" and foreign.consent_at is not None
        history = list(
            (await session.scalars(select(CheckInRow).where(CheckInRow.ward_id == wards[0]))).all()
        )
        assert len(history) == 1 and history[0].completed_at is not None
        assert (
            await session.scalar(
                select(func.count()).select_from(LinkCodeRow).where(LinkCodeRow.ward_id == wards[0])
            )
            == 0
        )
        attempts = set((await session.scalars(select(LinkAttemptRow.application_id))).all())
        assert attempts == {"device-1", "unattributed"}
    assert await snapshot() == (2, 2, 2, 2, 2, 3, 2, 2, 9)
    before = await snapshot()
    assert execute_operation("revoke-consent", wards[0]) == 0
    assert await snapshot() == before


async def test_delete_scope_and_cascade(seeded, sessionmaker, execute_operation, snapshot):
    families, wards = seeded
    assert execute_operation("delete-family-data", families[0]) == 0
    assert await snapshot() == (1, 1, 1, 1, 2, 2, 1, 2, 6)
    async with sessionmaker() as session:
        assert await session.get(WardRow, wards[0]) is None
        assert (await session.get(WardRow, wards[1])).device_id == "device-1"
        assert set((await session.scalars(select(OutboxRow.chat_id))).all()) == {102, 1000}
        assert set((await session.scalars(select(LinkAttemptRow.application_id))).all()) == {
            "device-1",
            "unattributed",
        }
    before = await snapshot()
    assert execute_operation("delete-family-data", families[0]) != 0
    assert await snapshot() == before


@pytest.mark.parametrize("script", ["revoke-consent", "delete-family-data"])
@pytest.mark.parametrize("target", [None, "not-a-uuid", str(uuid4())])
async def test_invalid_target_rolls_back(script, target, seeded, execute_operation, snapshot):
    before = await snapshot()
    assert execute_operation(script, target) != 0
    assert await snapshot() == before


@pytest.mark.parametrize("script", ["revoke-consent", "delete-family-data"])
async def test_error_after_deletions_rolls_back(
    script, seeded, sessionmaker, execute_operation, snapshot
):
    families, wards = seeded
    table = "check_ins" if script == "revoke-consent" else "families"
    before = await snapshot()
    async with sessionmaker() as session:
        await session.execute(
            text(
                "CREATE FUNCTION reject_privacy() RETURNS trigger LANGUAGE "
                "plpgsql AS $$ BEGIN RAISE EXCEPTION 'Injected failure'; END $$"
            )
        )
        await session.execute(
            text(
                f"CREATE TRIGGER reject_privacy BEFORE DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_privacy()"
            )
        )
        await session.commit()
    try:
        target = wards[0] if script == "revoke-consent" else families[0]
        assert execute_operation(script, target) != 0
        assert await snapshot() == before
        async with sessionmaker() as session:
            assert (await session.get(WardRow, wards[0])).device_id == "device-0"
    finally:
        async with sessionmaker() as session:
            await session.execute(text(f"DROP TRIGGER reject_privacy ON {table}"))
            await session.execute(text("DROP FUNCTION reject_privacy()"))
            await session.commit()
