import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.alice.dialog import CONSENT_VERSION, TOO_MANY_ATTEMPTS
from pereklichka.alice.router import get_session
from pereklichka.app import create_app
from pereklichka.config import Settings
from pereklichka.db.repositories import (
    CheckInRepository,
    FamilyRepository,
    LinkAttemptRepository,
    LinkCodeRepository,
    WardRepository,
)
from pereklichka.domain.family import Family, LinkCode, Ward
from tests.alice_protocol import APPLICATION_ID, SKILL_ID, number_entity, utterance


@pytest.fixture
async def app(
    database_url: str, sessionmaker: async_sessionmaker[AsyncSession]
) -> AsyncIterator[FastAPI]:
    app = create_app(Settings(database_url=database_url, skill_id=SKILL_ID))
    yield app
    await app.state.sessionmaker.kw["bind"].dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


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
    code = await LinkCodeRepository(session).issue(ward.id, datetime.now(UTC))
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
    assert linked.consent_version == CONSENT_VERSION


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
    await WardRepository(session).link_device(
        ward.id, APPLICATION_ID, datetime.now(UTC), CONSENT_VERSION
    )
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


class FailingCommitSession(AsyncSession):
    async def commit(self) -> None:
        raise ConnectionError("commit lost")


async def test_failed_commit_is_not_reported_as_success(
    app: FastAPI, client: AsyncClient, session: AsyncSession, link_code: LinkCode
) -> None:
    maker = async_sessionmaker(app.state.sessionmaker.kw["bind"], class_=FailingCommitSession)

    async def failing_session() -> AsyncIterator[AsyncSession]:
        async with maker() as failing:
            yield failing

    asked = await say(client, utterance(f"привязать код {link_code.code}", new=True))
    app.dependency_overrides[get_session] = failing_session
    reply = await client.post("/alice", json=utterance("да", state=asked["session_state"]))

    assert reply.status_code == 500
    assert await WardRepository(session).by_device(APPLICATION_ID) is None


async def test_wellbeing_is_kept_when_the_speaker_falls_silent(
    client: AsyncClient, session: AsyncSession, ward: Ward
) -> None:
    await WardRepository(session).link_device(
        ward.id, APPLICATION_ID, datetime.now(UTC), CONSENT_VERSION
    )
    await session.commit()

    greeting = await say(client, utterance("", new=True))
    await say(client, utterance("мне плохо", state=greeting["session_state"]))

    [checkin] = await CheckInRepository(session).for_ward(ward.id)
    assert (checkin.wellbeing, checkin.meds_taken, checkin.needs) == ("мне плохо", None, None)


async def test_code_guessing_is_stopped_after_five_failures(
    client: AsyncClient, link_code: LinkCode
) -> None:
    wrong = "000000" if link_code.code != "000000" else "999999"
    for _ in range(5):
        await say(client, utterance(f"привязать код {wrong}", new=True))

    blocked = await say(client, utterance(f"привязать код {link_code.code}", new=True))

    assert "согласны" not in blocked["response"]["text"]
    assert blocked["session_state"] == {}


async def test_global_cap_revokes_active_codes_and_refuses_new_speakers(
    client: AsyncClient, session: AsyncSession, link_code: LinkCode
) -> None:
    attempts = LinkAttemptRepository(session)
    now = datetime.now(UTC)
    for n in range(LinkCode.MAX_FAILED_ATTEMPTS_TOTAL - 1):
        await attempts.add_failure(f"forged-{n}", now)
    await session.commit()
    wrong = "000000" if link_code.code != "000000" else "999999"

    await say(client, utterance(f"привязать код {wrong}", new=True, application_id="last-guess"))
    blocked = await say(
        client,
        utterance(f"привязать код {link_code.code}", new=True, application_id="fresh-speaker"),
    )

    assert blocked["response"]["text"] == TOO_MANY_ATTEMPTS
    assert await LinkCodeRepository(session).find_active(link_code.code, now) is None


async def test_parallel_guesses_from_one_speaker_stop_at_the_limit(
    client: AsyncClient, link_code: LinkCode
) -> None:
    wrong = "000000" if link_code.code != "000000" else "999999"
    body = utterance(f"привязать код {wrong}", new=True)

    replies = await asyncio.gather(*(say(client, body) for _ in range(12)))

    answered = [r for r in replies if r["response"]["text"] != TOO_MANY_ATTEMPTS]
    assert len(answered) == LinkCode.MAX_FAILED_ATTEMPTS


async def test_answer_to_a_missing_checkin_is_an_error_not_a_goodbye(
    client: AsyncClient, session: AsyncSession, ward: Ward
) -> None:
    await WardRepository(session).link_device(
        ward.id, APPLICATION_ID, datetime.now(UTC), CONSENT_VERSION
    )
    await session.commit()

    state = {"step": "meds", "checkin": str(uuid4())}
    response = await client.post("/alice", json=utterance("да", state=state))

    assert response.status_code == 500
