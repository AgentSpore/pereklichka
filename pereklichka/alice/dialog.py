from datetime import datetime
from enum import StrEnum
from typing import Any

from pereklichka.alice.answers import extract_code, nothing_needed, yes_or_no
from pereklichka.alice.schemas import AliceRequest, AliceResponse, Reply
from pereklichka.db.repositories import CheckInRepository, LinkCodeRepository, WardRepository
from pereklichka.domain.checkin import CheckIn
from pereklichka.domain.family import Ward

HOW_TO_LINK = (
    "Здравствуйте! Это Перекличка. Чтобы привязать колонку, скажите: "
    "привязать код, и назовите шесть цифр из Телеграм-бота."
)
BAD_CODE = "Этот код не подошёл или устарел. Попросите у родных новый код и назовите его снова."
CONSENT = (
    "Перекличка будет каждое утро спрашивать о самочувствии и лекарствах и передавать "
    "ваши ответы родным в Телеграм. Голос не записывается. Вы согласны? Скажите да или нет."
)
SAY_YES_OR_NO = "Скажите, пожалуйста, да или нет."
LINKED = "Готово, колонка привязана. Утром скажите: Алиса, запусти Перекличку."
DECLINED = "Хорошо, я ничего не сохраняю. До свидания!"
GREETING = "Доброе утро, {name}! Как вы себя чувствуете?"
ASK_MEDS = "Лекарства сегодня приняли?"
ASK_NEEDS = "Нужно ли вам что-нибудь?"
GOODBYE = "Спасибо! Я всё передам родным. Хорошего вам дня!"


class Step(StrEnum):
    CONSENT = "consent"
    WELLBEING = "wellbeing"
    MEDS = "meds"
    NEEDS = "needs"


class Dialog:
    """One webhook turn: linking an unknown speaker, or the morning check-in of a known one."""

    def __init__(
        self,
        wards: WardRepository,
        codes: LinkCodeRepository,
        checkins: CheckInRepository,
        now: datetime,
    ) -> None:
        self._wards = wards
        self._codes = codes
        self._checkins = checkins
        self._now = now

    async def reply(self, request: AliceRequest) -> AliceResponse:
        state = {} if request.session.new else request.state.session
        ward = await self._wards.by_device(request.device_id)
        if ward is None:
            return await self._link(request, state)
        return await self._check_in(ward, request, state)

    async def _link(self, request: AliceRequest, state: dict[str, Any]) -> AliceResponse:
        if state.get("step") == Step.CONSENT:
            return await self._consent(request, state["code"])
        code = extract_code(request.request)
        if code is None:
            return self._say(HOW_TO_LINK)
        if await self._codes.find_active(code, self._now) is None:
            return self._say(BAD_CODE)
        return self._say(CONSENT, step=Step.CONSENT, code=code)

    async def _consent(self, request: AliceRequest, code: str) -> AliceResponse:
        answer = yes_or_no(request.request)
        if answer is None:
            return self._say(SAY_YES_OR_NO, step=Step.CONSENT, code=code)
        if not answer:
            return self._say(DECLINED, end=True)
        ward_id = await self._codes.consume(code, self._now)
        if ward_id is None:
            return self._say(BAD_CODE, end=True)
        await self._wards.link_device(ward_id, request.device_id, self._now)
        return self._say(LINKED, end=True)

    async def _check_in(
        self, ward: Ward, request: AliceRequest, state: dict[str, Any]
    ) -> AliceResponse:
        text = request.request.original_utterance or request.request.command
        match state.get("step"):
            case Step.WELLBEING if text:
                return self._say(ASK_MEDS, step=Step.MEDS, wellbeing=text)
            case Step.MEDS:
                meds = yes_or_no(request.request)
                return self._say(
                    ASK_NEEDS, step=Step.NEEDS, wellbeing=state["wellbeing"], meds=meds
                )
            case Step.NEEDS:
                await self._checkins.add(
                    CheckIn(
                        ward_id=ward.id,
                        at=self._now,
                        wellbeing=state["wellbeing"],
                        meds_taken=state["meds"],
                        needs=None if nothing_needed(request.request) else text,
                    )
                )
                return self._say(GOODBYE, end=True)
        return self._say(GREETING.format(name=ward.name), step=Step.WELLBEING)

    @staticmethod
    def _say(text: str, *, end: bool = False, **state: Any) -> AliceResponse:
        return AliceResponse(response=Reply(text=text, end_session=end), session_state=state)
