"""Yandex Dialogs webhook payloads, shaped as the official protocol documents them.

Request:  https://yandex.ru/dev/dialogs/alice/doc/ru/request
          https://yandex.ru/dev/dialogs/alice/doc/ru/request-simpleutterance
Entities: https://yandex.ru/dev/dialogs/alice/doc/ru/naming-entities
Response: https://yandex.ru/dev/dialogs/alice/doc/ru/response
"""

from copy import deepcopy
from typing import Any

SKILL_ID = "3ad36498-f5rd-4079-a14b-788652932056"
APPLICATION_ID = "47C73714B580ED2469056E71081159529FFC676A4E5B059D629A819E857DC2F8"
USER_ID = "6C91DA5198D1758C6A9F63A7C5CDDF09359F683B13A18A151FBF4C8B092BB0C2"

_BASE: dict[str, Any] = {
    "meta": {
        "locale": "ru-RU",
        "timezone": "Europe/Moscow",
        "client_id": "ru.yandex.searchplugin/7.16 (none none; android 4.4.2)",
        "interfaces": {"screen": {}, "account_linking": {}, "audio_player": {}},
    },
    "request": {
        "command": "",
        "original_utterance": "",
        "nlu": {"tokens": [], "entities": [], "intents": {}},
        "markup": {"dangerous_context": False},
        "type": "SimpleUtterance",
    },
    "session": {
        "message_id": 0,
        "session_id": "2eac4854-fce721f3-b845abba-20d60",
        "skill_id": SKILL_ID,
        "user_id": APPLICATION_ID,
        "application": {"application_id": APPLICATION_ID},
        "new": True,
    },
    "state": {"session": {}, "user": {}, "application": {}},
    "version": "1.0",
}


def utterance(
    command: str,
    *,
    new: bool = False,
    state: dict[str, Any] | None = None,
    entities: list[dict[str, Any]] | None = None,
    signed_in: bool = False,
    application_id: str = APPLICATION_ID,
) -> dict[str, Any]:
    """One SimpleUtterance request; `command` is already normalised the way Dialogs does it."""
    payload = deepcopy(_BASE)
    payload["request"].update(command=command, original_utterance=command)
    payload["request"]["nlu"].update(tokens=command.split(), entities=entities or [])
    payload["session"]["new"] = new
    payload["session"]["application"]["application_id"] = application_id
    payload["state"]["session"] = state or {}
    if signed_in:
        payload["session"]["user"] = {"user_id": USER_ID}
    return payload


def number_entity(start: int, value: int) -> dict[str, Any]:
    return {"tokens": {"start": start, "end": start + 1}, "type": "YANDEX.NUMBER", "value": value}
