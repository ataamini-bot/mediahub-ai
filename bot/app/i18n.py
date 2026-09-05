from typing import Any


SUPPORTED_LANGUAGES = frozenset({"fa", "en"})
DEFAULT_LANGUAGE = "fa"


MESSAGES: dict[str, dict[str, str]] = {
    "fa": {
        "start.welcome": "👋 <b>به MediaHub AI خوش آمدید!</b>",
        "start.instruction": "🎬 لینک رسانه را ارسال کنید تا بررسی شود.",
        "start.telegram_id": "🆔 Telegram ID شما: <code>{telegram_id}</code>",
        "start.registration_error": (
            "❌ <b>خطا در ثبت اطلاعات کاربر</b>\n\n"
            "لطفاً چند لحظه بعد دوباره تلاش کنید."
        ),
        "home.buy": "💎 خرید اشتراک",
        "home.subscription": "👤 وضعیت اشتراک من",
        "home.language": "🌐 تغییر زبان | Language",
        "home.admin": "⚙️ پنل مدیریت",
        "home.placeholder": "لینک رسانه را بفرستید…",
        "home.ready": "از منوی پایین یکی از گزینه‌ها را انتخاب کنید.",
        "language.changed": "✅ زبان ربات به فارسی تغییر کرد.",
        "language.failed": "❌ تغییر زبان انجام نشد. دوباره تلاش کنید.",
    },
    "en": {
        "start.welcome": "👋 <b>Welcome to MediaHub AI!</b>",
        "start.instruction": "🎬 Send a media link and I will inspect it.",
        "start.telegram_id": "🆔 Your Telegram ID: <code>{telegram_id}</code>",
        "start.registration_error": (
            "❌ <b>Could not register your account</b>\n\n"
            "Please try again in a moment."
        ),
        "home.buy": "💎 Buy subscription",
        "home.subscription": "👤 My subscription",
        "home.language": "🌐 Language | تغییر زبان",
        "home.admin": "⚙️ Admin panel",
        "home.placeholder": "Send a media link…",
        "home.ready": "Choose an option from the menu below.",
        "language.changed": "✅ The bot language was changed to English.",
        "language.failed": "❌ Could not change the language. Please retry.",
    },
}


def normalize_language(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    primary = normalized.split("-", 1)[0]
    return primary if primary in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def translate(language: str | None, key: str, **values: Any) -> str:
    normalized = normalize_language(language)
    template = MESSAGES.get(normalized, MESSAGES[DEFAULT_LANGUAGE]).get(key)

    if template is None:
        template = MESSAGES[DEFAULT_LANGUAGE].get(key, key)

    return template.format(**values)


HOME_ACTION_KEYS: dict[str, str] = {
    "home.buy": "buy",
    "home.subscription": "subscription",
    "home.language": "language",
    "home.admin": "admin",
}


HOME_BUTTON_ACTIONS: dict[str, str] = {
    translate(language, message_key): action
    for language in SUPPORTED_LANGUAGES
    for message_key, action in HOME_ACTION_KEYS.items()
}

# Keep keyboards sent by the previous release functional until Telegram
# replaces them with the new persistent menu on the user's next interaction.
HOME_BUTTON_ACTIONS.update(
    {
        "🌐 تغییر زبان": "language",
        "🌐 Change language": "language",
    }
)


def home_action_for_text(value: str | None) -> str | None:
    """Resolve a localized persistent-menu button without fuzzy matching."""
    return HOME_BUTTON_ACTIONS.get(str(value or "").strip())
