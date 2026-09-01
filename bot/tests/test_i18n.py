from app.i18n import normalize_language, translate
from app.keyboards.language import build_language_keyboard


def test_language_normalization():
    assert normalize_language("fa-IR") == "fa"
    assert normalize_language("en_US") == "en"
    assert normalize_language("de") == "en"


def test_translation_and_language_keyboard():
    assert "خوش آمدید" in translate("fa", "start.welcome")
    assert "Welcome" in translate("en", "start.welcome")

    keyboard = build_language_keyboard()
    callbacks = [
        button.callback_data
        for button in keyboard.inline_keyboard[0]
    ]
    assert callbacks == ["language:set:fa", "language:set:en"]
