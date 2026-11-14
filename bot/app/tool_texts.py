"""Validated administrator-editable tool messages; defaults are bilingual."""
import json
from pathlib import Path
from string import Formatter
import re

TOOL_TEXTS = json.loads(Path(__file__).with_name("media_tool_texts.json").read_text())


def validate_tool_text(key, value):
    entry = TOOL_TEXTS[key]
    if value in (entry["fa"], entry["en"]):
        return value
    if not isinstance(value, str) or not value.strip() or len(value) > entry["maximum"]:
        raise ValueError("Invalid message length")
    for _, field, spec, conversion in Formatter().parse(value):
        if field is not None and (field not in entry["fields"] or spec or conversion):
            raise ValueError("Use only the listed placeholders, without formatting")
    if entry.get("scope") == "common":
        # Existing translation fragments may deliberately contain half of an
        # HTML element. Preserve their markup boundaries and variable fields.
        tags = re.findall(r"<[^>]*>", value)
        if tags not in [re.findall(r"<[^>]*>", entry[language]) for language in ("fa", "en")]:
            raise ValueError("Preserve the original HTML tags")
    return value if entry.get("scope") == "common" else value.strip()


def tool_text(key, **values):
    import html
    from app.middleware.interface import ui_language
    from app.runtime_config import cached_configuration
    language = "en" if ui_language.get() == "en" else "fa"
    entry = TOOL_TEXTS[key]
    template = cached_configuration(language).get("tool_texts", {}).get(key, entry[language])
    try:
        template = validate_tool_text(key, template)
    except (ValueError, TypeError):
        template = entry[language]
    # Custom copy is plain text. Defaults alone may include built-in HTML.
    if entry["html"] and template != entry[language]:
        template = html.escape(template)
    return template.format(**values)


SOURCE_KEYS = {entry["source"]: key for key, entry in TOOL_TEXTS.items() if "source" in entry}


def authored_copy(source, language, default, *, key=None):
    key = key or SOURCE_KEYS.get(source)
    if not key:
        return default
    try:
        from app.runtime_config import cached_configuration
        candidate = cached_configuration(language).get("tool_texts", {}).get(key)
        if candidate is None or candidate == default:
            return default
        value = validate_tool_text(key, candidate)
        # Static translation fragments concatenate around dynamic values.
        leading = default[:len(default) - len(default.lstrip())]
        trailing = default[len(default.rstrip()):]
        return leading + value.strip() + trailing
    except (ImportError, AttributeError, ValueError, TypeError):
        return default
