SUPPORTED_LANGUAGES = frozenset({"fa", "en"})
DEFAULT_LANGUAGE = "en"


def normalize_language(value: str | None) -> str | None:
    """Normalize Telegram/IETF language values to a supported UI language."""
    normalized = str(value or "").strip().lower().replace("_", "-")

    if not normalized:
        return None

    primary = normalized.split("-", 1)[0]
    return primary if primary in SUPPORTED_LANGUAGES else None


def infer_language(telegram_language_code: str | None) -> str:
    """Choose the first UI language without treating metadata as preference."""
    return normalize_language(telegram_language_code) or DEFAULT_LANGUAGE


def effective_language(
    *,
    preferred_language: str | None,
    telegram_language_code: str | None,
) -> str:
    return (
        normalize_language(preferred_language)
        or infer_language(telegram_language_code)
    )
