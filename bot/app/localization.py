"""Translate authored UI text at render time; never rewrite user content."""
import json
from functools import wraps
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name('locales').joinpath('en.json').read_text(encoding='utf-8'))


def tr(value):
    from app.middleware.interface import ui_language
    if not isinstance(value, str):
        return value
    from app.tool_texts import authored_copy
    language = ui_language.get()
    default = CATALOG.get(value, value) if language == 'en' else value
    return authored_copy(value, language, default)


def _resolve(value):
    if isinstance(value, dict):
        return LocalizedDict(value)
    if isinstance(value, (tuple, list)):
        return LocalizedSequence(value)
    return tr(value)


class LocalizedDict(dict):
    def __getitem__(self, key):
        return _resolve(super().__getitem__(key))

    def get(self, key, default=None):
        return _resolve(super().get(key, default))

    def items(self):
        return ((key, _resolve(value)) for key, value in super().items())

    def values(self):
        return (_resolve(value) for value in super().values())


class LocalizedSequence(tuple):
    def __iter__(self):
        return (_resolve(value) for value in super().__iter__())

    def __getitem__(self, key):
        return _resolve(super().__getitem__(key))


def localized_collection(value):
    return _resolve(value)


def recipient_language(function):
    """Notifications use the recipient's preference, independently of the admin."""
    @wraps(function)
    async def wrapped(message, result, *args, **kwargs):
        from app.middleware.interface import ui_language
        language = (result.get("user") or {}).get("effective_language", "fa")
        token = ui_language.set(language)
        try:
            return await function(message, result, *args, **kwargs)
        finally:
            ui_language.reset(token)
    return wrapped
