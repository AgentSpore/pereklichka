import hashlib
import secrets
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pereklichka.bot import texts
from pereklichka.bot.schemas import ScheduleInput, WardInput
from pereklichka.db.relatives import RelativeRepository
from pereklichka.db.repositories import LinkCodeRepository, WardRepository
from pereklichka.domain.family import Ward


class FamilyService:
    """A scoped family service prevents commands from skipping membership checks."""

    def __init__(self, session: AsyncSession, chat_id: int, now: datetime) -> None:
        self._relatives = RelativeRepository(session)
        self._wards = WardRepository(session)
        self._codes = LinkCodeRepository(session)
        self._chat_id = chat_id
        self._now = now

    async def start(self, invitation: str | None) -> str:
        if invitation is None:
            await self._relatives.create_family(self._chat_id)
            return texts.FAMILY_READY
        await self._relatives.lock_chat(self._chat_id)
        digest = hashlib.sha256(invitation.encode()).hexdigest()
        family_id = await self._relatives.invitation(digest, self._now)
        if family_id is None:
            return texts.BAD_INVITE
        own = await self._relatives.family_for(self._chat_id)
        if own is not None and own != family_id:
            return texts.OTHER_FAMILY
        if own is None:
            await self._relatives.add_member(family_id, self._chat_id)
        return texts.JOINED

    async def command(self, command: str, args: str, username: str) -> str:
        family_id = await self._relatives.family_for(self._chat_id)
        if family_id is None:
            return texts.NO_FAMILY
        match command:
            case "ward":
                name, hour, timezone = (part.strip() for part in args.split("|"))
                data = WardInput(name=name, checkin_hour=int(hour), timezone=timezone)
                ward = Ward(family_id=family_id, **data.model_dump())
                await self._wards.add(ward)
                return texts.WARD_CREATED.format(name=ward.name, id=ward.id)
            case "wards":
                wards = await self._relatives.wards(family_id)
                return (
                    "\n".join(f"{w.name}: {w.id}, {w.checkin_hour}:00 {w.timezone}" for w in wards)
                    or texts.EMPTY
                )
            case "relatives":
                relatives = await self._relatives.members(family_id)
                return "\n".join(f"{r.alert_order}. {r.chat_id}" for r in relatives) or texts.EMPTY
            case "order":
                saved = await self._relatives.set_order(
                    family_id, [int(value) for value in args.split()]
                )
                return texts.SAVED if saved else texts.INVALID
            case "invite":
                token = secrets.token_urlsafe(24)
                await self._relatives.invite(
                    hashlib.sha256(token.encode()).hexdigest(),
                    family_id,
                    self._now + timedelta(days=1),
                )
                return texts.INVITE.format(username=username, token=token)
            case "code":
                ward = await self._relatives.ward(family_id, UUID(args.strip()))
                if ward is None:
                    return texts.NO_ACCESS
                code = await self._codes.issue(ward.id, self._now)
                return texts.CODE.format(code=code.code)
            case "schedule":
                return await self._schedule(family_id, args)
            case _:
                return texts.HELP

    async def _schedule(self, family_id: UUID, args: str) -> str:
        ward_id, hour, timezone, minutes = args.split()
        data = ScheduleInput(
            ward_id=UUID(ward_id),
            checkin_hour=int(hour),
            timezone=timezone,
            escalation_minutes=int(minutes),
        )
        ward = await self._relatives.ward(family_id, data.ward_id)
        if ward is None:
            return texts.NO_ACCESS
        await self._relatives.schedule(
            replace(
                ward,
                checkin_hour=data.checkin_hour,
                timezone=data.timezone,
                escalation_minutes=data.escalation_minutes,
            )
        )
        return texts.SAVED
