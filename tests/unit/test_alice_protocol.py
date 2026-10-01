import pytest
from fastapi.testclient import TestClient

from pereklichka.alice.answers import extract_code, yes_or_no
from pereklichka.alice.schemas import AliceRequest
from pereklichka.app import create_app
from tests.alice_protocol import APPLICATION_ID, USER_ID, number_entity, utterance


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app("postgresql+asyncpg://unused@localhost:1/unused"))


def test_device_is_yandex_user_when_signed_in_else_application() -> None:
    assert AliceRequest.model_validate(utterance("", signed_in=True)).device_id == USER_ID
    assert AliceRequest.model_validate(utterance("")).device_id == APPLICATION_ID


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
    assert extract_code(request) in {"123456", "012345"}
    assert extract_code(request) == command.removeprefix("привязать код ").replace(" ", "")


def test_no_code_in_plain_phrase() -> None:
    request = AliceRequest.model_validate(utterance("привязать колонку")).request
    assert extract_code(request) is None


@pytest.mark.parametrize(
    ("command", "expected"),
    [("да", True), ("да конечно", True), ("нет", False), ("не приняла", False), ("забыла", None)],
)
def test_yes_or_no(command: str, expected: bool | None) -> None:
    request = AliceRequest.model_validate(utterance(command)).request
    assert yes_or_no(request) is expected


def test_ping_is_answered_without_database(client: TestClient) -> None:
    body = utterance("ping")
    response = client.post("/alice", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "1.0"
    assert payload["response"]["end_session"] is True
    assert payload["response"]["text"]


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
