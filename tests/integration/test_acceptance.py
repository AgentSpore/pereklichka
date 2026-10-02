import asyncio
from datetime import UTC, datetime
from time import perf_counter

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from pereklichka.app import create_app
from pereklichka.config import Settings
from pereklichka.db.models import CheckInRow, OutboxRow
from pereklichka.db.relatives import RelativeRepository
from pereklichka.db.repositories import WardRepository
from pereklichka.domain.family import Ward
from tests.alice_protocol import SKILL_ID, utterance


@pytest.fixture
async def acceptance_wards(sessionmaker):
    wards = []
    async with sessionmaker() as session, session.begin():
        relatives = RelativeRepository(session)
        for index in range(20):
            family = await relatives.create_family(1000 + index * 2)
            await relatives.add_member(family, 1001 + index * 2)
            ward = Ward(
                family_id=family,
                name=f"Подопечный {index}",
                checkin_hour=9,
                timezone="Europe/Moscow",
                device_id=f"acceptance-{index}",
                consent_at=datetime.now(UTC),
                consent_version="synthetic-test",
            )
            await WardRepository(session).add(ward)
            wards.append(ward)
    return wards


async def check_in(client, index, ward):
    state = {}
    durations = []
    for text in ["", f"самочувствие {index}", "да", f"просьба {index}"]:
        final = utterance(text, new=not state, state=state, application_id=ward.device_id)
        started = perf_counter()
        response = await client.post("/alice", json=final)
        durations.append(perf_counter() - started)
        assert response.status_code == 200
        state = response.json()["session_state"]
    assert response.json()["response"]["end_session"] is True
    replay = await client.post("/alice", json=final)
    assert replay.status_code == 200
    assert replay.json()["response"]["end_session"] is True
    return durations


async def test_parallel_families_receive_only_their_completed_reports(
    database_url, sessionmaker, acceptance_wards, capsys
):
    wards = acceptance_wards
    app = create_app(Settings(database_url=database_url, skill_id=SKILL_ID))
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            samples = await asyncio.gather(
                *(check_in(client, i, ward) for i, ward in enumerate(wards))
            )
        async with sessionmaker() as session:
            checkins = list(await session.scalars(select(CheckInRow)))
            reports = list(await session.scalars(select(OutboxRow)))
            assert len(checkins) == 20 and all(row.completed_at for row in checkins)
            assert len(reports) == 40
            for index, ward in enumerate(wards):
                own = [row for row in checkins if row.ward_id == ward.id]
                assert len(own) == 1
                delivered = [row for row in reports if row.event_key == f"checkin:{own[0].id}"]
                assert {row.chat_id for row in delivered} == {1000 + index * 2, 1001 + index * 2}
                assert all(f"самочувствие {index}\n" in row.text for row in delivered)
                assert all(f"просьба {index}" in row.text for row in delivered)
        ordered = sorted(duration for sample in samples for duration in sample)
        with capsys.disabled():
            print(
                f"\nLocal ASGI/PostgreSQL: 20 concurrent families, {len(ordered)} turns, "
                f"p95={ordered[int(len(ordered) * 0.95) - 1]:.3f}s, max={max(ordered):.3f}s"
            )
    finally:
        await app.state.sessionmaker.kw["bind"].dispose()
