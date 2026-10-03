import pytest
from fastapi.testclient import TestClient

from pereklichka.alice.answers import extract_code, instruction_kind, yes_or_no
from pereklichka.alice.schemas import AliceRequest
from pereklichka.app import create_app
from pereklichka.config import Settings
from tests.alice_protocol import APPLICATION_ID, SKILL_ID, number_entity, utterance


@pytest.fixture
def client() -> TestClient:
    settings = Settings(database_url="postgresql+asyncpg://unused@localhost:1/x", skill_id=SKILL_ID)
    return TestClient(create_app(settings))


def test_device_is_the_application_even_for_a_signed_in_account() -> None:
    first = AliceRequest.model_validate(utterance("", signed_in=True))
    second = AliceRequest.model_validate(
        utterance("", signed_in=True, application_id="other-speaker")
    )

    assert first.device_id == APPLICATION_ID
    assert second.device_id == "other-speaker"


@pytest.mark.parametrize(
    ("command", "entities"),
    [
        ("привязать код 123456", [number_entity(2, 123456)]),
        (
            "привязать код 12 34 56",
            [number_entity(2, 12), number_entity(3, 34), number_entity(4, 56)],
        ),
        ("привязать код 1 2 3 4 5 6", [number_entity(2 + i, i + 1) for i in range(6)]),
        ("привязать код 012345", [number_entity(2, 12345)]),
        ("привязать код 123456", []),
    ],
)
def test_code_is_read_from_numbers_or_digits(command: str, entities: list) -> None:
    request = AliceRequest.model_validate(utterance(command, entities=entities)).request
    assert extract_code(request) == command.removeprefix("привязать код ").replace(" ", "")


def test_no_code_in_plain_phrase() -> None:
    request = AliceRequest.model_validate(utterance("привязать колонку")).request
    assert extract_code(request) is None


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("да", True),
        ("да конечно", True),
        ("да не забыла", True),
        ("да приняла не беспокойтесь", True),
        ("да не против", True),
        ("нет", False),
        ("не приняла", False),
        ("да нет", None),
        ("забыла", None),
    ],
)
def test_yes_or_no(command: str, expected: bool | None) -> None:
    request = AliceRequest.model_validate(utterance(command)).request
    assert yes_or_no(request) is expected


def test_ping_is_answered_without_database(client: TestClient) -> None:
    response = client.post("/alice", json=utterance("ping"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "1.0"
    assert payload["response"]["end_session"] is True
    assert payload["response"]["text"]
    assert "tts" not in payload["response"]


def test_foreign_skill_is_rejected(client: TestClient) -> None:
    body = utterance("ping")
    body["session"]["skill_id"] = "someone-else"

    assert client.post("/alice", json=body).status_code == 403


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("command", "expected"),
    [("помощь", "help"), ("Помощь!", "help"), ("  ЧТО ТЫ УМЕЕШЬ?  ", "capabilities")],
)
def test_help_recognises_whole_commands(command: str, expected: str) -> None:
    request = AliceRequest.model_validate(utterance(command)).request
    assert instruction_kind(request) == expected


def test_help_intent_survives_protocol_validation() -> None:
    body = utterance("подскажи")
    body["request"]["nlu"]["intents"] = {"YANDEX.HELP": {"slots": {}}}
    request = AliceRequest.model_validate(body).request
    assert request.nlu.intents == {"YANDEX.HELP": {"slots": {}}}
    assert instruction_kind(request) == "help"


def test_help_falls_back_to_original_utterance() -> None:
    body = utterance("")
    body["request"]["original_utterance"] = "Что ты умеешь?"
    assert instruction_kind(AliceRequest.model_validate(body).request) == "capabilities"
