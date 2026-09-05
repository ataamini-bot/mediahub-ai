SUPPORTED_LANGUAGES = frozenset({"fa", "en"})
DEFAULT_LANGUAGE = "fa"


def normalize_language(value: str | None) -> str | None:
    """Normalize Telegram/IETF language values to a supported UI language."""
    normalized = str(value or "").strip().lower().replace("_", "-")

    if not normalized:
        return None

    primary = normalized.split("-", 1)[0]
    return primary if primary in SUPPORTED_LANGUAGES else None


def infer_language(telegram_language_code: str | None) -> str:
    """Use Persian initially; Telegram metadata is not an explicit choice."""
    return DEFAULT_LANGUAGE


def effective_language(
    *,
    preferred_language: str | None,
    telegram_language_code: str | None,
) -> str:
    return (
        normalize_language(preferred_language)
        or infer_language(telegram_language_code)
    )
