"""Compact music cards and player controls, with editable bilingual copy."""
import html
from uuid import UUID

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.tool_texts import tool_text as text


def recovery_keyboard(*, no_match=False):
    rows = []
    if no_match:
        rows.append([InlineKeyboardButton(text=text("music_no_match_why"), callback_data="tool:why")])
    rows += [
        [InlineKeyboardButton(text=text("music_try_another"), callback_data="tool:open:music")],
        [InlineKeyboardButton(text=text("music_request_review"), callback_data="support:open")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def music_file_presentation(music, job_id):
    request_id = UUID(str(music["request_id"])).hex
    index = int(music["candidate"])
    rows = [
        [InlineKeyboardButton(text=text("other_formats"), callback_data=f"tool:source:{request_id}:{index}")],
        [InlineKeyboardButton(text=text("music_try_another"), callback_data="tool:open:music"),
         InlineKeyboardButton(text=text("music_file_details"), callback_data=f"download_details:{int(job_id)}")],
    ]
    caption = text("music_audio_caption", title=html.escape(music["title"]), artist=html.escape(music["artist"]))
    return caption, InlineKeyboardMarkup(inline_keyboard=rows)


async def update_music_card(message, body, *, reply_markup=None):
    """Edit the processing card; keep the preview when there is one."""
    try:
        if getattr(message, "video", None):
            await message.edit_caption(caption=body, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await message.edit_text(body, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await message.answer(body, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
