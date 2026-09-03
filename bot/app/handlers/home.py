from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.admin import show_admin_panel_message
from app.handlers.payments import (
    send_payment_offers_menu,
    send_subscription_status,
)
from app.i18n import (
    HOME_BUTTON_ACTIONS,
    home_action_for_text,
    normalize_language,
    translate,
)
from app.keyboards.language import build_language_keyboard
from app.keyboards.payment import build_home_reply_keyboard
from app.services.backend import register_telegram_user


router = Router(name="home")
router.message.filter(F.chat.type == "private")


@router.message(F.text.in_(set(HOME_BUTTON_ACTIONS)))
async def persistent_home_button(
    message: Message,
    state: FSMContext,
) -> None:
    """Route persistent-menu labels before the generic download handler."""
    action = home_action_for_text(message.text)
    if action is None or message.from_user is None:
        return

    fallback_language = normalize_language(message.from_user.language_code)

    try:
        user = await register_telegram_user(message)
    except Exception:
        await state.clear()
        await message.answer(
            translate(fallback_language, "start.registration_error"),
            parse_mode="HTML",
            reply_markup=build_home_reply_keyboard(fallback_language),
        )
        return

    language = normalize_language(user.get("effective_language"))
    include_admin = bool(user.get("is_admin"))
    await state.clear()

    if action == "buy":
        await send_payment_offers_menu(message, state)
        return

    if action == "subscription":
        await send_subscription_status(message, message.from_user.id)
        return

    if action == "language":
        await message.answer(
            "🌐 <b>زبان ربات را انتخاب کنید / Choose the bot language:</b>",
            parse_mode="HTML",
            reply_markup=build_language_keyboard(),
        )
        return

    if action == "admin":
        if not include_admin:
            return
        await show_admin_panel_message(
            message,
            state,
            register_user=False,
        )
        return

    await message.answer(
        translate(language, "home.ready"),
        reply_markup=build_home_reply_keyboard(
            language,
            include_admin=include_admin,
        ),
    )
