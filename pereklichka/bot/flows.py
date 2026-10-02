import secrets
from collections.abc import Sequence
from html import escape
from uuid import UUID

from aiogram.fsm.context import FSMContext
from pydantic import ValidationError

from pereklichka.bot import presentation, texts
from pereklichka.bot.schemas import DraftInput, Screen, WardInput
from pereklichka.bot.service import FamilyService

STEPS = ("name", "hour", "zone", "confirm")
PROMPTS = {
    "name": texts.NAME_PROMPT,
    "hour": texts.HOUR_PROMPT,
    "zone": texts.ZONE_PROMPT,
    "minutes": texts.MINUTES_PROMPT,
}


async def begin_draft(state: FSMContext, service: FamilyService, ward_id: UUID | None) -> Screen:
    await service.list_wards()
    data = {
        "nonce": secrets.token_hex(4),
        "name": "",
        "checkin_hour": 9,
        "timezone": "Europe/Moscow",
        "escalation_minutes": 30,
    }
    if ward_id is not None:
        ward = await service.get_ward(ward_id)
        if ward is None:
            return Screen(texts.UX_NO_ACCESS, presentation.navigation())
        data.update(
            name=ward.name,
            checkin_hour=ward.checkin_hour,
            timezone=ward.timezone,
            escalation_minutes=ward.escalation_minutes,
            ward_id=str(ward.id),
            linked=bool(ward.device_id),
        )
    await state.set_data(data)
    await state.set_state("hour" if ward_id else "name")
    return await draft_screen(state)


async def draft_screen(state: FSMContext, error: bool = False) -> Screen:
    data = await state.get_data()
    step = await state.get_state()
    if step not in {*PROMPTS, "confirm"}:
        return Screen(texts.STALE, presentation.navigation())
    prefix = f"ux:draft:{data['nonce']}:"
    items = []
    if step == "confirm":
        draft = DraftInput.model_validate(data)
        text = texts.CONFIRM_TITLE + texts.CARD.format(
            name=escape(draft.name),
            hour=draft.checkin_hour,
            timezone=escape(draft.timezone),
            minutes=draft.escalation_minutes,
            linked=texts.LINKED if data.get("linked") else texts.UNLINKED,
        )
        items.append((texts.CONFIRM_SAVE if draft.ward_id else texts.CONFIRM_ADD, prefix + "save"))
    else:
        steps = ("hour", "zone", "minutes", "confirm") if data.get("ward_id") else STEPS
        text = PROMPTS[str(step)].format(step=steps.index(str(step)) + 1)
        if step == "zone":
            items.append((texts.MOSCOW, prefix + "zone"))
    if step != "name":
        items.append((texts.BACK, prefix + "back"))
    items.append((texts.CANCEL, prefix + "cancel"))
    return Screen((texts.DRAFT_INVALID if error else "") + text, presentation.keyboard(items))


async def enter_value(state: FSMContext, value: str) -> Screen:
    step = await state.get_state()
    data = await state.get_data()
    try:
        match step:
            case "name":
                data["name"] = value
            case "hour":
                data["checkin_hour"] = int(value)
            case "zone":
                data["timezone"] = value.strip()
            case "minutes":
                data["escalation_minutes"] = int(value)
            case _:
                return await draft_screen(state)
        WardInput.model_validate({**data, "name": data["name"] or "draft"})
    except (ValueError, ValidationError):
        return await draft_screen(state, error=True)
    await state.set_data(data)
    steps = ("hour", "zone", "minutes", "confirm") if data.get("ward_id") else STEPS
    await state.set_state(steps[steps.index(step) + 1])
    return await draft_screen(state)


async def draft_action(state: FSMContext, service: FamilyService, parts: Sequence[str]) -> Screen:
    data = await state.get_data()
    step = await state.get_state()
    if len(parts) != 4 or not step or parts[2] != data.get("nonce"):
        return Screen(texts.STALE, presentation.navigation())
    action = parts[3]
    if action == "cancel":
        return Screen(texts.MENU, presentation.menu(), clear_draft=True)
    if action == "save" and step == "confirm":
        ward = await service.save_draft(DraftInput.model_validate(data))
        return Screen(
            (texts.SAVED if data.get("ward_id") else texts.CREATED) + presentation.ward_card(ward),
            presentation.ward_actions(ward),
            clear_draft=True,
        )
    if action == "zone" and step == "zone":
        return await enter_value(state, "Europe/Moscow")
    if action == "back":
        steps = ("hour", "zone", "minutes", "confirm") if data.get("ward_id") else STEPS
        index = steps.index(step)
        if index == 0:
            await state.clear()
            return Screen(texts.MENU, presentation.menu())
        await state.set_state(steps[index - 1])
        return await draft_screen(state)
    return Screen(texts.STALE, presentation.navigation())


async def open_screen(action: str, service: FamilyService, username: str) -> Screen:
    match action:
        case "menu":
            return Screen(texts.MENU, presentation.menu())
        case "help":
            return Screen(texts.UX_HELP, presentation.menu())
        case "invite":
            return Screen(await service.command("invite", "", username), presentation.navigation())
        case "relatives":
            return Screen(await service.relative_labels(), presentation.navigation())
        case "wards":
            wards = await service.list_wards()
            items = [(ward.name, f"ux:ward:{ward.id}") for ward in wards]
            items.extend([(texts.ADD, "ux:add"), (texts.MENU_BUTTON, "ux:menu")])
            return Screen(
                texts.WARDS_TITLE if wards else texts.EMPTY_WARDS, presentation.keyboard(items)
            )
        case _:
            return Screen(texts.STALE, presentation.navigation())


async def ward_screen(action: str, ward_id: UUID, service: FamilyService) -> Screen:
    ward = await service.get_ward(ward_id)
    if ward is None:
        return Screen(texts.UX_NO_ACCESS, presentation.navigation())
    text = presentation.ward_card(ward)
    if action == "code":
        text = escape(await service.command("code", str(ward_id), ""))
    return Screen(text, presentation.ward_actions(ward))
