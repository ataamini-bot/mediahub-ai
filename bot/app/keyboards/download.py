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


def _media_output_keyboard(
    reference: str, output: str, *, persistent: bool, source_media_type: str = "video",
) -> InlineKeyboardMarkup:
    from app.middleware.interface import ui_language
    english = ui_language.get() == "en"
    callbacks = {
        kind: f"download_more:{kind}:{reference}" if persistent else (
            f"audio:open:{reference}" if kind == "audio" else f"media:{kind}:{reference}"
        )
        for kind in ("video", "audio", "cover", "description")
    }
    repeat = (
        f"download_again:{reference}"
        if persistent and output not in {"cover", "description"}
        else callbacks.get(output, f"download_again:{reference}")
    )
    rows = [[
        InlineKeyboardButton(text="🔄 Download again" if english else "🔄 دانلود مجدد", callback_data=repeat),
        InlineKeyboardButton(text="📋 Details" if english else "📋 جزئیات", callback_data=(
            f"download_details:{reference}" if persistent else f"media:details:{reference}"
        )),
    ]]
    labels = {
        "video": "🎬 Video qualities" if english else "🎬 کیفیت‌های ویدئو",
        "audio": "🎵 Extract audio" if english else "🎵 استخراج صدا",
        "cover": "🖼 Extract cover" if english else "🖼 استخراج کاور",
        "description": "📝 Description" if english else "📝 توضیحات",
    }
    if output not in {"convert", "unknown"}:
        available = ["video", "audio", "cover", "description"]
        if source_media_type == "image":
            available = ["description"]
        elif source_media_type == "audio":
            available.remove("video")
        buttons = [InlineKeyboardButton(text=labels[kind], callback_data=callbacks[kind])
                   for kind in available if kind != output]
        rows.extend(buttons[i:i + 2] for i in range(0, len(buttons), 2))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_completed_download_keyboard(
    job_id: int, *, media_type: str | None = None, source_media_type: str = "video",
) -> InlineKeyboardMarkup:
    """Completed-job references survive restart and are checked against the owner."""
    return _media_output_keyboard(str(job_id), media_type or "unknown", persistent=True,
                                  source_media_type=source_media_type)


def build_selection_output_keyboard(
    token: str, *, media_type: str, source_media_type: str = "video",
) -> InlineKeyboardMarkup:
    return _media_output_keyboard(token, media_type, persistent=False,
                                  source_media_type=source_media_type)
