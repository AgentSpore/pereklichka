import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pereklichka.alice.dialog import CONSENT_VERSION, TOO_MANY_ATTEMPTS
from pereklichka.alice.router import get_session
from pereklichka.app import create_app
from pereklichka.config import Settings
from pereklichka.db.models import Base, OutboxRow
from pereklichka.db.relatives import RelativeRepository
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


async def test_help_explains_the_skill_to_an_unknown_speaker(client: AsyncClient) -> None:
    reply = await say(client, utterance("Помощь", new=True))

    assert "самочувствии" in reply["response"]["text"]
    assert "лекарствах" in reply["response"]["text"]
    assert "Телеграм" in reply["response"]["text"]
    assert "привязать код" in reply["response"]["text"]
    assert reply["response"]["end_session"] is False
    assert reply["session_state"] == {}


@pytest.mark.parametrize("linked", [False, True])
async def test_greeting_explains_the_skill_and_next_answer(
    client: AsyncClient, session: AsyncSession, ward: Ward, linked: bool
) -> None:
    if linked:
        await WardRepository(session).link_device(
            ward.id, APPLICATION_ID, datetime.now(UTC), CONSENT_VERSION
        )
        await session.commit()
    reply = await say(client, utterance("", new=True))
    text = reply["response"]["text"]

    assert "Семейная перекличка" in text
    assert "самочувствии" in text and "лекарствах" in text and "просьбах" in text
    assert "согласия" in text and "Телеграм" in text
    assert "помощь" in text
    assert ("Как вы себя чувствуете?" if linked else "привязать код") in text


async def test_linking_explains_manual_launch(client: AsyncClient, link_code: LinkCode) -> None:
    asked = await say(client, utterance(f"привязать код {link_code.code}", new=True))
    assert "Когда вы запускаете" in asked["response"]["text"]
    assert "Навык не сохраняет аудиозаписи" in asked["response"]["text"]
    done = await say(client, utterance("да", state=asked["session_state"]))
    assert "Алиса, запусти Семейную перекличку" in done["response"]["text"]


@pytest.fixture(params=["unknown", "consent", "start", "wellbeing", "meds", "needs"])
async def dialog_stage(
    request: pytest.FixtureRequest, client: AsyncClient, session: AsyncSession, link_code: LinkCode
) -> tuple[str, dict[str, Any]]:
    stage = request.param
    if stage == "unknown":
        return stage, {}
    if stage == "consent":
        reply = await say(client, utterance(f"привязать код {link_code.code}"))
        return stage, reply["session_state"]
    await WardRepository(session).link_device(
        link_code.ward_id, APPLICATION_ID, datetime.now(UTC), CONSENT_VERSION
    )
    ward = await WardRepository(session).by_device(APPLICATION_ID)
    assert ward is not None
    await RelativeRepository(session).add_member(ward.family_id, 101)
    await session.commit()
    if stage == "start":
        return stage, {}
    reply = await say(client, utterance("", new=True))
    if stage in {"meds", "needs"}:
        reply = await say(client, utterance("хорошо", state=reply["session_state"]))
    if stage == "needs":
        reply = await say(client, utterance("да", state=reply["session_state"]))
    return stage, reply["session_state"]


async def dialog_rows(session: AsyncSession) -> dict[str, list]:
    """Snapshot real test-database rows without an ORM identity cache."""
    return {
        table.name: list((await session.execute(select(table))).tuples())
        for table in Base.metadata.sorted_tables
    }


@pytest.mark.parametrize("command", ["Помощь", "Что ты умеешь", "подскажи"])
async def test_help_preserves_each_stage_without_writes(
    client: AsyncClient, session: AsyncSession, dialog_stage: tuple[str, dict], command: str
) -> None:
    stage, state = dialog_stage
    if stage not in {"unknown", "start"}:
        state = {**state, "text": "keep", "end": True}
    body = utterance(command, state=state, new=stage in {"unknown", "start"})
    if command == "подскажи":
        body["request"]["nlu"]["intents"] = {"YANDEX.HELP": {"slots": {}}}
    before = await dialog_rows(session)
    reply = await say(client, body)
    assert await dialog_rows(session) == before
    expected_state = {"step": "wellbeing"} if stage == "start" else state
    assert reply["session_state"] == expected_state
    assert reply["response"]["end_session"] is False
    text = reply["response"]["text"]
    assert "самочувствии" in text and "лекарствах" in text and "просьбах" in text
    assert "помощь" in text
    if command == "Что ты умеешь":
        assert "если отметки нет" in text
    else:
        assert "что ты умеешь" in text and "привязать код" in text
    prompts = {
        "unknown": "привязать код",
        "consent": "да или нет",
        "start": "Как вы себя чувствуете?",
        "wellbeing": "Как вы себя чувствуете?",
        "meds": "Лекарства сегодня приняли?",
        "needs": "Нужно ли вам что-нибудь?",
    }
    assert prompts[stage] in text
    answer = {
        "unknown": "",
        "consent": "да",
        "start": "хорошо",
        "wellbeing": "хорошо",
        "meds": "да",
        "needs": "купите хлеба",
    }[stage]
    continued = await say(client, utterance(answer, state=reply["session_state"]))
    if stage in {"consent", "needs"}:
        assert continued["response"]["end_session"] is True
    elif stage in {"start", "wellbeing"}:
        assert continued["session_state"]["step"] == "meds"
    elif stage == "meds":
        assert continued["session_state"]["step"] == "needs"
    if stage == "needs":
        rows = list((await session.execute(select(OutboxRow.text))).scalars())
        assert len(rows) == 1 and "купите хлеба" in rows[0]


@pytest.mark.parametrize("command", ["Помощь", "Что ты умеешь"])
@pytest.mark.parametrize("linked", [False, True])
async def test_new_help_session_discards_stale_state(
    client: AsyncClient, session: AsyncSession, ward: Ward, command: str, linked: bool
) -> None:
    if linked:
        await WardRepository(session).link_device(
            ward.id, APPLICATION_ID, datetime.now(UTC), CONSENT_VERSION
        )
        await session.commit()
    stale = {"step": "needs" if linked else "consent", "checkin": str(uuid4()), "code": "123456"}
    before = await dialog_rows(session)
    reply = await say(client, utterance(command, new=True, state=stale))
    assert reply["session_state"] == ({"step": "wellbeing"} if linked else {})
    assert await dialog_rows(session) == before


@pytest.mark.parametrize("command", ["нужна помощь с покупками", "что ты умеешь готовить"])
async def test_free_answer_containing_help_is_saved(
    client: AsyncClient, session: AsyncSession, ward: Ward, command: str
) -> None:
    await WardRepository(session).link_device(
        ward.id, APPLICATION_ID, datetime.now(UTC), CONSENT_VERSION
    )
    await session.commit()
    greeting = await say(client, utterance("", new=True))
    meds = await say(client, utterance(command, state=greeting["session_state"]))
    needs = await say(client, utterance("нет", state=meds["session_state"]))
    await say(client, utterance(command, state=needs["session_state"]))
    [checkin] = await CheckInRepository(session).for_ward(ward.id)
    assert checkin.wellbeing == command and checkin.needs == command


@pytest.mark.parametrize("linked", [False, True])
async def test_welcome_identifies_the_bot_with_accessible_link(
    client: AsyncClient, session: AsyncSession, ward: Ward, linked: bool
) -> None:
    if linked:
        await WardRepository(session).link_device(
            ward.id, APPLICATION_ID, datetime.now(UTC), CONSENT_VERSION
        )
        await session.commit()
    before = await dialog_rows(session)
    reply = await say(client, utterance("", new=True))
    response = reply["response"]
    assert "@PereklichkaAppBot" in response["text"]
    assert "https://t.me/PereklichkaAppBot" in response["text"]
    assert response["buttons"] == [
        {"title": "Открыть Telegram-бота", "url": "https://t.me/PereklichkaAppBot", "hide": False}
    ]
    assert "Перекличка апп бот" in response["tts"]
    assert "https://" not in response["tts"]
    assert len(response["text"]) <= 1024 and len(response["tts"]) <= 1024
    assert await dialog_rows(session) == before


async def test_help_and_capabilities_are_distinct_at_each_stage(
    client: AsyncClient, session: AsyncSession, dialog_stage: tuple[str, dict]
) -> None:
    stage, state = dialog_stage
    before = await dialog_rows(session)
    help_body = utterance("Помощь", state=state, new=stage in {"unknown", "start"})
    capabilities_body = utterance("Что ты умеешь", state=state, new=stage in {"unknown", "start"})
    help_reply = await say(client, help_body)
    capabilities_reply = await say(client, capabilities_body)
    capabilities_body["request"]["nlu"]["intents"] = {"YANDEX.HELP": {"slots": {}}}
    with_intent = await say(client, capabilities_body)
    assert help_reply["response"]["text"] != capabilities_reply["response"]["text"]
    assert with_intent["response"] == capabilities_reply["response"]
    assert help_reply["session_state"] == capabilities_reply["session_state"]
    assert await dialog_rows(session) == before
    assert "Добавить близкого" in help_reply["response"]["text"]
    assert "если отметки нет" in capabilities_reply["response"]["text"]
    for reply in [help_reply, capabilities_reply]:
        response = reply["response"]
        assert "@PereklichkaAppBot" in response["text"]
        assert response["buttons"][0]["url"] == "https://t.me/PereklichkaAppBot"
        assert "Перекличка апп бот" in response["tts"]
        assert "https://" not in response["tts"]
        assert len(response["text"]) <= 1024 and len(response["tts"]) <= 1024
