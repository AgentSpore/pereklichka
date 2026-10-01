from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.app import create_app
from pereklichka.db.repositories import (
    CheckInRepository,
    FamilyRepository,
    LinkCodeRepository,
    WardRepository,
)
from pereklichka.domain.family import Family, LinkCode, Ward
from tests.alice_protocol import APPLICATION_ID, number_entity, utterance


@pytest.fixture
async def client(
    database_url: str, sessionmaker: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    app = create_app(database_url)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        yield client
    await app.state.sessionmaker.kw["bind"].dispose()


@pytest.fixture
async def ward(session: AsyncSession) -> Ward:
    family = Family()
    await FamilyRepository(session).add(family)
    ward = Ward(family_id=family.id, name="Анна Петровна", checkin_hour=9, timezone="Europe/Moscow")
    await WardRepository(session).add(ward)
    await session.commit()
    return ward


@pytest.fixture
async def link_code(session: AsyncSession, ward: Ward) -> LinkCode:
    code = LinkCode.issue(ward.id, datetime.now(UTC))
    await LinkCodeRepository(session).add(code)
    await session.commit()
    return code


async def say(client: AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/alice", json=body)
    assert response.status_code == 200
    return response.json()


async def test_unknown_speaker_is_linked_after_consent(
    client: AsyncClient, session: AsyncSession, ward: Ward, link_code: LinkCode
) -> None:
    hello = await say(client, utterance("", new=True))
    assert "привязать код" in hello["response"]["text"]

    command = f"привязать код {link_code.code}"
    asked = await say(client, utterance(command, entities=[number_entity(2, int(link_code.code))]))
    assert "согласны" in asked["response"]["text"]
    assert asked["response"]["end_session"] is False

    done = await say(client, utterance("да", state=asked["session_state"]))
    assert done["response"]["end_session"] is True

    linked = await WardRepository(session).by_device(APPLICATION_ID)
    assert linked is not None
    assert linked.id == ward.id
    assert linked.consent_at is not None


async def test_refused_consent_links_nothing(
    client: AsyncClient, session: AsyncSession, link_code: LinkCode
) -> None:
    asked = await say(client, utterance(f"привязать код {link_code.code}", new=True))
    done = await say(client, utterance("нет", state=asked["session_state"]))

    assert done["response"]["end_session"] is True
    assert await WardRepository(session).by_device(APPLICATION_ID) is None
    assert await LinkCodeRepository(session).find_active(link_code.code, datetime.now(UTC))


async def test_wrong_code_is_refused(client: AsyncClient, link_code: LinkCode) -> None:
    wrong = "000000" if link_code.code != "000000" else "999999"
    reply = await say(client, utterance(f"привязать код {wrong}", new=True))

    assert "не подошёл" in reply["response"]["text"]
    assert reply["session_state"] == {}


async def test_linked_speaker_checks_in(
    client: AsyncClient, session: AsyncSession, ward: Ward
) -> None:
    await WardRepository(session).link_device(ward.id, APPLICATION_ID, datetime.now(UTC))
    await session.commit()

    greeting = await say(client, utterance("", new=True))
    assert "Анна Петровна" in greeting["response"]["text"]
    meds = await say(client, utterance("хорошо", state=greeting["session_state"]))
    needs = await say(client, utterance("да", state=meds["session_state"]))
    bye = await say(client, utterance("купите хлеба", state=needs["session_state"]))

    assert bye["response"]["end_session"] is True
    [checkin] = await CheckInRepository(session).for_ward(ward.id)
    assert (checkin.wellbeing, checkin.meds_taken, checkin.needs) == (
        "хорошо",
        True,
        "купите хлеба",
    )
