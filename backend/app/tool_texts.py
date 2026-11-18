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
