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
                        "⏸ توقف دانلود"
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
                        "❌ انصراف از دانلود"
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
                        "▶️ ادامه دانلود"
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
                        "❌ انصراف از دانلود"
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
