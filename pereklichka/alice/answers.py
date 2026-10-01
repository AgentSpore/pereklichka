import re

from pereklichka.alice.schemas import Utterance

CODE_LENGTH = 6
YES = frozenset({"да", "ага", "конечно", "согласен", "согласна", "принял", "приняла"})
NO = frozenset({"нет", "не", "неа", "ничего"})
NOTHING = NO | {"мне", "нужно", "надо", "спасибо", "всё", "все", "есть"}
DIGITS = re.compile(r"\d+")


def extract_code(request: Utterance) -> str | None:
    """Six digits spoken as one number, as groups, or one by one.

    Dialogs turns numerals into YANDEX.NUMBER entities, but a single number drops a
    leading zero, so the digits in the normalised command are the second source.
    """
    numbers = [str(e.value) for e in request.nlu.entities if e.type == "YANDEX.NUMBER"]
    for candidate in ("".join(numbers), "".join(DIGITS.findall(request.command))):
        if len(candidate) == CODE_LENGTH and candidate.isdigit():
            return candidate
    return None


def yes_or_no(request: Utterance) -> bool | None:
    words = set(request.nlu.tokens or request.command.split())
    if words & NO:
        return False
    if words & YES:
        return True
    return None


def nothing_needed(request: Utterance) -> bool:
    words = set(request.nlu.tokens or request.command.split())
    return not words or words <= NOTHING
