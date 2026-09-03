from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.i18n import translate
from app.keyboards.language import build_language_keyboard
from app.keyboards.payment import build_home_reply_keyboard
from app.services.backend import (
    BackendAPIError,
    register_telegram_user,
    set_user_language,
)


router = Router(name="language")


@router.message(Command("language"))
async def language_command(message: Message, state: FSMContext) -> None:
    try:
        await register_telegram_user(message)
    except Exception:
        await message.answer(
            "❌ اتصال به Backend برقرار نشد / Backend is unavailable."
        )
        return

    await state.clear()
    await message.answer(
        "🌐 <b>زبان ربات را انتخاب کنید / Choose the bot language:</b>",
        parse_mode="HTML",
        reply_markup=build_language_keyboard(),
    )


@router.callback_query(F.data == "language:open")
async def open_language_menu(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🌐 <b>زبان ربات را انتخاب کنید / Choose the bot language:</b>",
            parse_mode="HTML",
            reply_markup=build_language_keyboard(),
        )

    await callback.answer()


@router.callback_query(F.data.startswith("language:set:"))
async def select_language(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    language = callback.data.rsplit(":", 1)[-1]

    if language not in {"fa", "en"}:
        await callback.answer("Invalid language", show_alert=True)
        return

    try:
        user = await set_user_language(callback.from_user.id, language)
        await callback.message.edit_text(
            translate(language, "language.changed"),
            parse_mode="HTML",
        )
        await callback.message.answer(
            translate(language, "home.ready"),
            reply_markup=build_home_reply_keyboard(
                language=language,
                include_admin=(
                    bool(user.get("is_admin"))
                    and callback.message.chat.type == "private"
                ),
            ),
        )
        await callback.answer()
    except (BackendAPIError, OSError):
        await callback.answer(
            translate(language, "language.failed"),
            show_alert=True,
        )
