"""Yandex Dialogs webhook protocol, version 1.0.

https://yandex.ru/dev/dialogs/alice/doc/ru/request
https://yandex.ru/dev/dialogs/alice/doc/ru/response
"""

from typing import Any

from pydantic import BaseModel, Field


class Entity(BaseModel):
    type: str
    value: Any = None


class Nlu(BaseModel):
    tokens: list[str] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)


class Utterance(BaseModel):
    type: str
    command: str = ""
    original_utterance: str = ""
    nlu: Nlu = Field(default_factory=Nlu)


class User(BaseModel):
    user_id: str


class Application(BaseModel):
    application_id: str


class Session(BaseModel):
    new: bool
    session_id: str
    message_id: int
    user: User | None = None
    application: Application


class State(BaseModel):
    session: dict[str, Any] = Field(default_factory=dict)


class AliceRequest(BaseModel):
    request: Utterance
    session: Session
    state: State = Field(default_factory=State)
    version: str

    @property
    def device_id(self) -> str:
        """Yandex account when signed in, otherwise the app instance; both stable per skill."""
        if self.session.user is not None:
            return self.session.user.user_id
        return self.session.application.application_id


class Reply(BaseModel):
    text: str
    tts: str | None = None
    end_session: bool


class AliceResponse(BaseModel):
    response: Reply
    session_state: dict[str, Any] = Field(default_factory=dict)
    version: str = "1.0"
