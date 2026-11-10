from app.localization import tr as _tr, localized_collection as _localized_collection
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_tr("🇮🇷 فارسی"),
                    callback_data="language:set:fa",
                ),
                InlineKeyboardButton(
                    text="🇬🇧 English",
                    callback_data="language:set:en",
                ),
            ],
        ]
    )
