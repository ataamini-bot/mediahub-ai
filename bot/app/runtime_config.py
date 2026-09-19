import asyncio
import re
import time
from typing import Any

from app.i18n import HOME_BUTTON_ACTIONS, normalize_language, translate
from app.services.backend import get_bot_configuration


CACHE_TTL_SECONDS = 30
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_PERSIAN_TEXT_RE = re.compile(r"[\u0600-\u06ff]")
_BUTTON_STYLES = {"default", "primary", "success", "danger"}
_DEFAULT_BUTTON_STYLES = {
    "buy": "success",
    "subscription": "primary",
    "support": "primary",
    "convert": "primary",
    "admin": "danger",
}
HOME_MENU_BUTTON_KEYS = (
    "buy",
    "subscription",
    "support",
    "language",
    "convert",
    "tutorial",
    "faq",
    "admin",
)
_DEFAULT_HOME_LAYOUT = {
    "columns": 2,
    "order": list(HOME_MENU_BUTTON_KEYS),
}


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
            "محتوای خصوصی یا نیازمند ورود قابل دریافت نیست. سهمیه پلن رایگان هفتگی است و حجم پخش‌های چندبخشی پیش از دانلود برآورد می‌شود."
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
            "❓ FAQ\n\nPrivate or login-only media is unavailable. The Free plan quota resets weekly. Segmented-stream sizes are estimates."
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
            "convert": translate(normalized, "home.convert"),
            "tutorial": "📘 آموزش استفاده" if normalized == "fa" else "📘 How to use",
            "faq": "❓ سوالات متداول" if normalized == "fa" else "❓ FAQ",
            "admin": translate(normalized, "home.admin"),
            "check_membership": (
                "✅ بررسی عضویت" if normalized == "fa" else "✅ Check membership"
            ),
            "back_home": "🏠 منوی اصلی" if normalized == "fa" else "🏠 Main menu",
        },
        "button_styles": dict(_DEFAULT_BUTTON_STYLES),
        "home_layout": {
            "columns": _DEFAULT_HOME_LAYOUT["columns"],
            "order": list(_DEFAULT_HOME_LAYOUT["order"]),
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
        styles = result.get("button_styles")
        result["button_styles"] = {
            **fallback["button_styles"],
            **{
                str(key): str(value).strip().lower()
                for key, value in (styles.items() if isinstance(styles, dict) else [])
                if str(value).strip().lower() in _BUTTON_STYLES
            },
        }
        result["home_layout"] = runtime_home_layout(
            {
                "home_layout": result.get("home_layout"),
            }
        )
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
        if isinstance(value, str) and value.strip() and not (
            language == "en" and _PERSIAN_TEXT_RE.search(value)
        ):
            return value.strip()
    return FALLBACK_CONTENT[language].get(key, key)


def runtime_button(configuration: dict, key: str) -> str:
    buttons = configuration.get("buttons")
    if isinstance(buttons, dict):
        value = buttons.get(key)
        language = normalize_language(configuration.get("language")) or "fa"
        if isinstance(value, str) and value.strip() and not (
            language == "en" and _PERSIAN_TEXT_RE.search(value)
        ):
            return value.strip()
    return str(fallback_configuration(configuration.get("language", "fa"))["buttons"].get(key, key))


def runtime_button_style(configuration: dict, key: str) -> str:
    styles = configuration.get("button_styles")
    if isinstance(styles, dict):
        value = str(styles.get(key) or "").strip().lower()
        if value in _BUTTON_STYLES:
            return value
    return str(fallback_configuration(configuration.get("language", "fa"))["button_styles"].get(key, "default"))


def runtime_home_layout(configuration: dict) -> dict[str, Any]:
    """Normalize the admin-controlled order and row width of the reply menu."""

    layout = configuration.get("home_layout") if isinstance(configuration, dict) else None
    columns = _DEFAULT_HOME_LAYOUT["columns"]
    order = list(_DEFAULT_HOME_LAYOUT["order"])
    if not isinstance(layout, dict):
        return {"columns": columns, "order": order}

    candidate_columns = layout.get("columns")
    if isinstance(candidate_columns, int) and not isinstance(candidate_columns, bool) and candidate_columns in {1, 2, 3}:
        columns = candidate_columns

    candidate_order = layout.get("order")
    if isinstance(candidate_order, list):
        cleaned: list[str] = []
        for key in candidate_order:
            if key in HOME_MENU_BUTTON_KEYS and key not in cleaned:
                cleaned.append(key)
        # A partial/corrupt setting must never hide a primary action. Append
        # missing keys in the default order while keeping every valid move.
        cleaned.extend(key for key in HOME_MENU_BUTTON_KEYS if key not in cleaned)
        order = cleaned
    return {"columns": columns, "order": order}


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
        "convert": "convert",
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
