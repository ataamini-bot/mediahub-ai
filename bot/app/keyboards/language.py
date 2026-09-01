from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🇮🇷 فارسی",
                    callback_data="language:set:fa",
                ),
                InlineKeyboardButton(
                    text="🇬🇧 English",
                    callback_data="language:set:en",
                ),
            ],
        ]
    )
