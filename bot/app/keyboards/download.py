from app.localization import tr as _tr, localized_collection as _localized_collection
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)


def build_active_download_keyboard(
    job_id: int,
) -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        _tr("⏸ توقف دانلود")
                    ),
                    callback_data=(
                        f"download_pause:"
                        f"{job_id}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        _tr("❌ انصراف از دانلود")
                    ),
                    callback_data=(
                        f"download_cancel:"
                        f"{job_id}"
                    ),
                ),
            ],
        ]
    )


def build_paused_download_keyboard(
    job_id: int,
) -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        _tr("▶️ ادامه دانلود")
                    ),
                    callback_data=(
                        f"download_resume:"
                        f"{job_id}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        _tr("❌ انصراف از دانلود")
                    ),
                    callback_data=(
                        f"download_cancel:"
                        f"{job_id}"
                    ),
                ),
            ],
        ]
    )


# ============================================================
# Resolution helpers
# ============================================================
