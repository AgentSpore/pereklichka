import pytest

from pereklichka.alice.answers import nothing_needed
from pereklichka.alice.schemas import Nlu, Utterance


@pytest.mark.parametrize("tokens", [True, False])
@pytest.mark.parametrize(
    "command", ["мне нужно", "мне всё нужно", "всё надо", "нужно", "мне не всё нужно"]
)
def test_affirmative_need_is_not_discarded(command: str, tokens: bool) -> None:
    request = Utterance(
        type="SimpleUtterance",
        command=command,
        nlu=Nlu(tokens=command.split() if tokens else []),
    )
    assert nothing_needed(request) is False


@pytest.mark.parametrize("tokens", [True, False])
@pytest.mark.parametrize(
    "command", ["", "нет", "ничего", "мне не нужно", "ничего не надо", "всё есть", "спасибо"]
)
def test_explicit_no_need_still_recognised(command: str, tokens: bool) -> None:
    request = Utterance(
        type="SimpleUtterance",
        command=command,
        nlu=Nlu(tokens=command.split() if tokens else []),
    )
    assert nothing_needed(request) is True


@pytest.mark.parametrize("command", ["купите хлеба", "мне нужно молоко", "нет хлеба"])
def test_specific_request_is_preserved(command: str) -> None:
    assert nothing_needed(Utterance(type="SimpleUtterance", command=command)) is False
