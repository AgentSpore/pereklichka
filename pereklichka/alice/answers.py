import re
from itertools import pairwise

from pereklichka.alice.schemas import Utterance

CODE_LENGTH = 6
YES = frozenset({"да", "ага", "конечно", "согласен", "согласна", "принял", "приняла"})
NEGATION = "не"
NO = frozenset({"нет", "неа"})
NOTHING = NO | {NEGATION, "ничего", "мне", "нужно", "надо", "спасибо", "всё", "все", "есть"}
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
    """Yes, no, or None when unclear or mixed; "не" negates only the word right after it."""
    words = request.nlu.tokens or request.command.split()
    yes = no = False
    for previous, word in pairwise(["", *words]):
        if word in YES and previous == NEGATION:
            no = True
        elif word in YES:
            yes = True
        elif word in NO:
            no = True
    return None if yes == no else yes


def nothing_needed(request: Utterance) -> bool:
    words = set(request.nlu.tokens or request.command.split())
    return not words or words <= NOTHING
