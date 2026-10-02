from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pereklichka.bot import texts
from pereklichka.domain.family import Ward


def keyboard(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Use one full-width button per action for readable mobile navigation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data)] for label, data in items
        ]
    )


def menu() -> InlineKeyboardMarkup:
    return keyboard(
        [
            (texts.MY_WARDS, "ux:wards"),
            (texts.ADD, "ux:add"),
            (texts.INVITE_BUTTON, "ux:invite"),
            (texts.RELATIVES_BUTTON, "ux:relatives"),
            (texts.HELP_BUTTON, "ux:help"),
        ]
    )


def navigation() -> InlineKeyboardMarkup:
    return keyboard([(texts.MENU_BUTTON, "ux:menu")])


def ward_card(ward: Ward) -> str:
    return texts.CARD.format(
        name=escape(ward.name),
        hour=ward.checkin_hour,
        timezone=escape(ward.timezone),
        minutes=ward.escalation_minutes,
        linked=texts.LINKED if ward.device_id else texts.UNLINKED,
    )


def ward_actions(ward: Ward) -> InlineKeyboardMarkup:
    return keyboard(
        [
            (texts.CODE_BUTTON, f"ux:code:{ward.id}"),
            (texts.SETTINGS_BUTTON, f"ux:settings:{ward.id}"),
            (texts.MY_WARDS, "ux:wards"),
            (texts.MENU_BUTTON, "ux:menu"),
        ]
    )
