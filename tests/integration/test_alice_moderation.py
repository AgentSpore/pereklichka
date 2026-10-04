import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.app import create_app
from pereklichka.config import Settings
from pereklichka.db.models import CheckInRow, LinkCodeRow, OutboxRow, RelativeRow, WardRow
from pereklichka.db.relatives import RelativeRepository
from pereklichka.db.repositories import FamilyRepository, WardRepository
from pereklichka.domain.family import Family, Ward
from tests.alice_protocol import SKILL_ID, utterance

CODE = "814527"  # Public synthetic moderation identifier, never a real-family credential.
CHAT = -100123456


@pytest.fixture
async def moderation_app(database_url: str, session: AsyncSession):
    family = Family()
    await FamilyRepository(session).add(family)
    await RelativeRepository(session).add_member(family.id, CHAT)
    await session.commit()
    app = create_app(
        Settings(
            database_url=database_url,
            skill_id=SKILL_ID,
            moderation_family_id=family.id,
            moderation_recipient_chat_id=CHAT,
            moderation_until=datetime.now(UTC) + timedelta(days=7),
        )
    )
    yield app
    await app.state.sessionmaker.kw["bind"].dispose()


async def test_repeatable_access_links_two_applications_and_queues_real_reports(
    moderation_app, session: AsyncSession
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=moderation_app), base_url="http://test"
    ) as client:
        for device in ["reviewer-a", "reviewer-b"]:
            asked = await client.post(
                "/alice", json=utterance(f"привязать код {CODE}", application_id=device)
            )
            assert asked.status_code == 200
            assert "согласны" in asked.json()["response"]["text"]
            linked = await client.post(
                "/alice",
                json=utterance("да", state=asked.json()["session_state"], application_id=device),
            )
            assert linked.json()["response"]["end_session"] is True
            body = utterance("", new=True, application_id=device)
            for answer in ["хорошо", "да", "ничего"]:
                reply = (await client.post("/alice", json=body)).json()
                body = utterance(answer, state=reply["session_state"], application_id=device)
            done = await client.post("/alice", json=body)
            assert done.json()["response"]["end_session"] is True
            await client.post("/alice", json=body)
    wards = list((await session.execute(select(WardRow))).scalars())
    reports = list((await session.execute(select(OutboxRow))).scalars())
    assert len(wards) == 2 and {w.device_id for w in wards} == {"reviewer-a", "reviewer-b"}
    assert len(reports) == 2 and {r.chat_id for r in reports} == {CHAT}


@pytest.fixture
async def moderation_client(moderation_app):
    async with AsyncClient(
        transport=ASGITransport(app=moderation_app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize(
    "reason", ["off", "expired", "family", "recipient", "extra", "collision", "ordinary"]
)
async def test_invalid_access_fails_closed(moderation_app, moderation_client, session, reason):
    settings = moderation_app.state.settings
    if reason == "off":
        settings.moderation_family_id = settings.moderation_recipient_chat_id = (
            settings.moderation_until
        ) = None
    elif reason == "expired":
        settings.moderation_until = datetime.now(UTC) - timedelta(seconds=1)
    elif reason == "family":
        settings.moderation_family_id = uuid4()
    elif reason == "recipient":
        await session.execute(delete(RelativeRow))
    elif reason == "extra":
        await RelativeRepository(session).add_member(settings.moderation_family_id, -123)
    else:
        ward = WardRow(
            id=uuid4(),
            family_id=settings.moderation_family_id,
            name="Synthetic normal",
            consent_version="moderation:collision-fixture" if reason == "collision" else None,
            checkin_hour=9,
            timezone="Europe/Moscow",
        )
        session.add(ward)
        await session.flush()
        if reason == "collision":
            session.add(
                LinkCodeRow(
                    id=uuid4(),
                    ward_id=ward.id,
                    code=CODE,
                    expires_at=datetime.now(UTC) + timedelta(minutes=15),
                )
            )
    await session.commit()
    before = list((await session.execute(select(WardRow.id))).scalars())
    response = await moderation_client.post("/alice", json=utterance(f"привязать код {CODE}"))
    assert response.json()["response"]["end_session"] is True
    assert list((await session.execute(select(WardRow.id))).scalars()) == before
    assert list((await session.execute(select(OutboxRow))).scalars()) == []


async def test_consent_decline_help_and_parallel_confirmation(moderation_client, session):
    asked = (await moderation_client.post("/alice", json=utterance(f"привязать код {CODE}"))).json()
    state = asked["session_state"]
    for command in ["Помощь", "Что ты умеешь", "Открыть Telegram-бота", "не знаю"]:
        reply = (
            await moderation_client.post("/alice", json=utterance(command, state=state))
        ).json()
        assert reply["session_state"] == state
        assert list((await session.execute(select(WardRow))).scalars()) == []
    declined = await moderation_client.post("/alice", json=utterance("нет", state=state))
    assert declined.json()["response"]["end_session"] is True
    assert list((await session.execute(select(WardRow))).scalars()) == []
    async with asyncio.TaskGroup() as group:
        confirmations = [
            group.create_task(moderation_client.post("/alice", json=utterance("да", state=state)))
            for _ in range(2)
        ]
    assert all(task.result().status_code == 200 for task in confirmations)
    wards = list((await session.execute(select(WardRow))).scalars())
    assert len(wards) == 1 and wards[0].consent_version.startswith("moderation:")
    assert len(wards[0].consent_version) == 64


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("disable", [False, True])
async def test_expiry_and_disable_close_pending_and_linked_access(
    moderation_app, moderation_client, session, linked, disable
):
    asked = (await moderation_client.post("/alice", json=utterance(f"привязать код {CODE}"))).json()
    if linked:
        await moderation_client.post("/alice", json=utterance("да", state=asked["session_state"]))
    settings = moderation_app.state.settings
    if disable:
        settings.moderation_family_id = settings.moderation_recipient_chat_id = (
            settings.moderation_until
        ) = None
    else:
        settings.moderation_until = datetime.now(UTC) - timedelta(seconds=1)
    body = (
        utterance("хорошо", state={"step": "wellbeing"})
        if linked
        else utterance("да", state=asked["session_state"])
    )
    reply = await moderation_client.post("/alice", json=body)
    assert reply.json()["response"]["end_session"] is True
    assert list((await session.execute(select(CheckInRow))).scalars()) == []
    assert list((await session.execute(select(OutboxRow))).scalars()) == []


async def test_test_access_never_overwrites_real_binding_or_membership(moderation_client, session):
    family = Family()
    await FamilyRepository(session).add(family)
    await RelativeRepository(session).add_member(family.id, 101)
    ward = Ward(
        family_id=family.id,
        name="Synthetic ordinary",
        checkin_hour=9,
        timezone="Europe/Moscow",
        device_id="real-bound",
    )
    await WardRepository(session).add(ward)
    await session.commit()
    members = list((await session.execute(select(RelativeRow.id))).scalars())
    await moderation_client.post(
        "/alice",
        json=utterance("да", state={"step": "consent", "code": CODE}, application_id="real-bound"),
    )
    found = await WardRepository(session).by_device("real-bound")
    assert found == ward
    assert list((await session.execute(select(RelativeRow.id))).scalars()) == members
    assert list((await session.execute(select(WardRow.id))).scalars()) == [ward.id]
