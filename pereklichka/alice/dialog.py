import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.alice.answers import asks_for_help, extract_code, nothing_needed, yes_or_no
from pereklichka.alice.schemas import AliceRequest, AliceResponse, Reply
from pereklichka.bot.reports import ReportService
from pereklichka.db.repositories import (
    CheckInRepository,
    LinkAttemptRepository,
    LinkCodeRepository,
    WardRepository,
)
from pereklichka.domain.checkin import CheckIn
from pereklichka.domain.family import LinkCode, Ward

INTRO = (
    "Семейная перекличка спрашивает о самочувствии, лекарствах и просьбах. "
    "После вашего согласия ответы передаются родным в Телеграм. "
)
HOW_TO_LINK = (
    "Здравствуйте! " + INTRO + "Чтобы привязать колонку, скажите: "
    "привязать код, затем назовите шесть цифр из бота Переклички в Телеграм. "
    "Для инструкции скажите: помощь."
)
BAD_CODE = "Этот код не подошёл или устарел. Попросите у родных новый код и назовите его снова."
CONSENT = (
    "Когда вы запускаете Семейную перекличку, я спрашиваю о самочувствии, лекарствах и просьбах "
    "и передаю "
    "ваши ответы родным в Телеграм. Навык не сохраняет аудиозаписи. "
    "Вы согласны? Скажите да или нет."
)
CONSENT_VERSION = hashlib.sha256(CONSENT.encode()).hexdigest()
TOO_MANY_ATTEMPTS = "Сейчас привязка недоступна, попробуйте через пятнадцать минут."
SAY_YES_OR_NO = "Скажите, пожалуйста, да или нет."
LINKED = "Готово, колонка привязана. Для отметки скажите: Алиса, запусти Семейную перекличку."
DECLINED = "Хорошо, я ничего не сохраняю. До свидания!"
GREETING = (
    "Здравствуйте, {name}! " + INTRO + "Для инструкции скажите: помощь. Как вы себя чувствуете?"
)
ASK_MEDS = "Лекарства сегодня приняли?"
ASK_NEEDS = "Нужно ли вам что-нибудь?"
GOODBYE = "Спасибо! Я всё передам родным. Хорошего вам дня!"
HELP = (
    INTRO + "Для привязки скажите: привязать код, затем шесть цифр из бота Переклички. "
    "На вопрос о согласии и лекарствах отвечайте да или нет. "
    "Самочувствие и просьбы описывайте своими словами. Если ничего не нужно, скажите: ничего. "
    "Команды помощь и что ты умеешь повторят инструкцию. "
    "Для выхода скажите: Алиса, хватит. Разговор запускаете вы сами. "
)


class Step(StrEnum):
    CONSENT = "consent"
    WELLBEING = "wellbeing"
    MEDS = "meds"
    NEEDS = "needs"


class Dialog:
    """One webhook turn: linking an unknown speaker, or the morning check-in of a known one."""

    def __init__(self, session: AsyncSession, now: datetime) -> None:
        self._wards = WardRepository(session)
        self._codes = LinkCodeRepository(session)
        self._attempts = LinkAttemptRepository(session)
        self._checkins = CheckInRepository(session)
        self._now = now
        self._reports = ReportService(session, now)

    async def reply(self, request: AliceRequest) -> AliceResponse:
        state = {} if request.session.new else request.state.session
        ward = await self._wards.by_device(request.device_id)
        if asks_for_help(request.request):
            return self._help(ward, state)
        if ward is None:
            return await self._link(request, state)
        return await self._check_in(ward, request, state)

    def _help(self, ward: Ward | None, state: dict[str, Any]) -> AliceResponse:
        if ward is None:
            prompt = (
                SAY_YES_OR_NO
                if state.get("step") == Step.CONSENT
                else "Сейчас скажите: привязать код, затем назовите шесть цифр."
            )
        else:
            state = state or {"step": Step.WELLBEING}
            prompt = {
                Step.WELLBEING: "Как вы себя чувствуете?",
                Step.MEDS: ASK_MEDS + " Скажите да или нет.",
                Step.NEEDS: ASK_NEEDS + " Назовите просьбу или скажите: ничего.",
            }.get(state.get("step"), "Как вы себя чувствуете?")
        return AliceResponse(
            response=Reply(text=HELP + prompt, end_session=False), session_state=state
        )

    async def _link(self, request: AliceRequest, state: dict[str, Any]) -> AliceResponse:
        if state.get("step") == Step.CONSENT:
            return await self._consent(request, state["code"])
        code = extract_code(request.request)
        if code is None:
            return self._say(HOW_TO_LINK)
        if await self._too_many_failures(request.device_id):
            return self._say(TOO_MANY_ATTEMPTS, end=True)
        if await self._codes.find_active(code, self._now) is None:
            await self._record_failure(request.device_id)
            return self._say(BAD_CODE)
        return self._say(CONSENT, step=Step.CONSENT, code=code)

    async def _too_many_failures(self, device_id: str) -> bool:
        # INVARIANT(pereklichka-link): serialises count→insert;
        # without it parallel guesses pass the cap
        await self._attempts.lock()
        since = self._now - LinkCode.TTL
        own = await self._attempts.failures_since(device_id, since)
        total = await self._attempts.failures_since(None, since)
        return own >= LinkCode.MAX_FAILED_ATTEMPTS or total >= LinkCode.MAX_FAILED_ATTEMPTS_TOTAL

    async def _record_failure(self, device_id: str) -> None:
        await self._attempts.add_failure(device_id, self._now)
        total = await self._attempts.failures_since(None, self._now - LinkCode.TTL)
        # The cap refuses further guesses before they are counted, so it is crossed once per window.
        if total == LinkCode.MAX_FAILED_ATTEMPTS_TOTAL:
            revoked = await self._codes.revoke_active(self._now)
            logger.warning("Link-code guessing cap reached, {} active codes revoked", revoked)

    async def _consent(self, request: AliceRequest, code: str) -> AliceResponse:
        answer = yes_or_no(request.request)
        if answer is None:
            return self._say(SAY_YES_OR_NO, step=Step.CONSENT, code=code)
        if not answer:
            return self._say(DECLINED, end=True)
        ward_id = await self._codes.consume(code, self._now)
        if ward_id is None:
            return self._say(BAD_CODE, end=True)
        await self._wards.link_device(ward_id, request.device_id, self._now, CONSENT_VERSION)
        return self._say(LINKED, end=True)

    async def _check_in(
        self, ward: Ward, request: AliceRequest, state: dict[str, Any]
    ) -> AliceResponse:
        text = request.request.original_utterance or request.request.command
        match state.get("step"):
            case Step.WELLBEING if text:
                checkin = CheckIn(
                    ward_id=ward.id, at=self._now, wellbeing=text, meds_taken=None, needs=None
                )
                await self._checkins.add(checkin)
                return self._say(ASK_MEDS, step=Step.MEDS, checkin=str(checkin.id))
            case Step.MEDS:
                checkin_id = UUID(state["checkin"])
                await self._checkins.set_meds(checkin_id, ward.id, yes_or_no(request.request))
                return self._say(ASK_NEEDS, step=Step.NEEDS, checkin=state["checkin"])
            case Step.NEEDS:
                needs = None if nothing_needed(request.request) else text
                await self._reports.complete(UUID(state["checkin"]), ward, needs)
                return self._say(GOODBYE, end=True)
        return self._say(GREETING.format(name=ward.name), step=Step.WELLBEING)

    @staticmethod
    def _say(text: str, *, end: bool = False, **state: Any) -> AliceResponse:
        return AliceResponse(response=Reply(text=text, end_session=end), session_state=state)
