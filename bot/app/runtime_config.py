import asyncio
import time
from typing import Any

from app.i18n import HOME_BUTTON_ACTIONS, normalize_language, translate
from app.services.backend import get_bot_configuration


CACHE_TTL_SECONDS = 30
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


FALLBACK_CONTENT: dict[str, dict[str, str]] = {
    "fa": {
        "welcome_title": "👋 به MediaHub AI خوش آمدید!",
        "welcome_instruction": "🎬 لینک رسانه را ارسال کنید تا بررسی شود.",
        "tutorial": (
            "📘 آموزش استفاده از ربات\n\n"
            "لینک رسانه را کپی و برای ربات ارسال کنید؛ سپس رسانه و کیفیت دلخواه را انتخاب کنید."
        ),
        "faq": (
            "❓ سوالات متداول\n\n"
            "محتوای خصوصی یا نیازمند ورود قابل دریافت نیست. حجم پخش‌های چندبخشی پیش از دانلود برآورد می‌شود."
        ),
        "support_intro": "موضوع درخواست پشتیبانی را انتخاب کنید:",
        "support_prompt": "پیام خود را در یک نوبت بفرستید.",
        "support_sent": "✅ درخواست شما ثبت شد و برای مدیران مرتبط ارسال شد.",
        "forced_join": "ابتدا در کانال‌های زیر عضو شوید و سپس عضویت را بررسی کنید.",
        "membership_verified": "✅ عضویت تأیید شد؛ اکنون لینک را دوباره بفرستید.",
    },
    "en": {
        "welcome_title": "👋 Welcome to MediaHub AI!",
        "welcome_instruction": "🎬 Send a media link and I will inspect it.",
        "tutorial": "📘 How to use\n\nSend a media link, then choose an item and quality.",
        "faq": (
            "❓ FAQ\n\nPrivate or login-only media is unavailable. Segmented-stream sizes are estimates."
        ),
        "support_intro": "Choose the subject of your support request:",
        "support_prompt": "Send your request in one message.",
        "support_sent": "✅ Your request was sent to the relevant administrators.",
        "forced_join": "Join the channels below, then check your membership.",
        "membership_verified": "✅ Membership verified. Send the link again now.",
    },
}


def fallback_configuration(language: str) -> dict[str, Any]:
    normalized = normalize_language(language) or "fa"
    return {
        "language": normalized,
        "content": dict(FALLBACK_CONTENT[normalized]),
        "buttons": {
            "buy": translate(normalized, "home.buy"),
            "subscription": translate(normalized, "home.subscription"),
            "language": translate(normalized, "home.language"),
            "support": "🛟 پشتیبانی" if normalized == "fa" else "🛟 Support",
            "tutorial": "📘 آموزش استفاده" if normalized == "fa" else "📘 How to use",
            "faq": "❓ سوالات متداول" if normalized == "fa" else "❓ FAQ",
            "admin": translate(normalized, "home.admin"),
            "check_membership": (
                "✅ بررسی عضویت" if normalized == "fa" else "✅ Check membership"
            ),
            "back_home": "🏠 منوی اصلی" if normalized == "fa" else "🏠 Main menu",
        },
        "custom_buttons": [],
        "required_channels": [],
    }


async def runtime_configuration(
    language: str,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    normalized = normalize_language(language) or "fa"
    now = time.monotonic()
    cached = _cache.get(normalized)
    if not refresh and cached is not None and cached[0] > now:
        return cached[1]

    try:
        result = await get_bot_configuration(normalized)
        if not isinstance(result, dict):
            raise TypeError("Bot configuration response is not an object")
        fallback = fallback_configuration(normalized)
        content = result.get("content")
        buttons = result.get("buttons")
        result["content"] = {
            **fallback["content"],
            **(content if isinstance(content, dict) else {}),
        }
        result["buttons"] = {
            **fallback["buttons"],
            **(buttons if isinstance(buttons, dict) else {}),
        }
        result["custom_buttons"] = list(result.get("custom_buttons") or [])
        result["required_channels"] = list(result.get("required_channels") or [])
    except Exception:
        result = fallback_configuration(normalized)

    _cache[normalized] = (now + CACHE_TTL_SECONDS, result)
    return result


async def all_runtime_configurations() -> tuple[dict[str, Any], dict[str, Any]]:
    fa, en = await asyncio.gather(
        runtime_configuration("fa"),
        runtime_configuration("en"),
    )
    return fa, en


def clear_runtime_configuration_cache() -> None:
    _cache.clear()


def runtime_content(configuration: dict, key: str) -> str:
    language = normalize_language(configuration.get("language")) or "fa"
    content = configuration.get("content")
    if isinstance(content, dict):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return FALLBACK_CONTENT[language].get(key, key)


def runtime_button(configuration: dict, key: str) -> str:
    buttons = configuration.get("buttons")
    if isinstance(buttons, dict):
        value = buttons.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(fallback_configuration(configuration.get("language", "fa"))["buttons"].get(key, key))


def action_for_runtime_text(
    text: str,
    configurations: tuple[dict[str, Any], dict[str, Any]],
) -> dict[str, Any] | None:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return None

    legacy_action = HOME_BUTTON_ACTIONS.get(normalized_text)
    if legacy_action is not None:
        return {"action": legacy_action}

    key_actions = {
        "buy": "buy",
        "subscription": "subscription",
        "language": "language",
        "support": "support",
        "tutorial": "tutorial",
        "faq": "faq",
        "admin": "admin",
        "back_home": "home",
    }
    for configuration in configurations:
        for key, action in key_actions.items():
            if normalized_text == runtime_button(configuration, key):
                return {"action": action}
        language = normalize_language(configuration.get("language")) or "fa"
        label_key = f"label_{language}"
        for button in configuration.get("custom_buttons", []):
            if isinstance(button, dict) and normalized_text == str(button.get(label_key) or "").strip():
                return {"action": "custom", "button": button}
    return None

