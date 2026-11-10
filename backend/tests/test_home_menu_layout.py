import pytest

from app.services.application_settings import SettingValidationError
from app.services.managed_settings import HOME_MENU_BUTTON_KEYS, validate_managed_setting


def test_home_menu_layout_accepts_complete_one_to_three_column_layout():
    value = {
        "columns": 3,
        "order": list(reversed(HOME_MENU_BUTTON_KEYS)),
    }

    assert validate_managed_setting("bot.home_layout.en", value) == value


@pytest.mark.parametrize(
    "value",
    [
        {"columns": 0, "order": list(HOME_MENU_BUTTON_KEYS)},
        {"columns": 4, "order": list(HOME_MENU_BUTTON_KEYS)},
        {"columns": 2, "order": list(HOME_MENU_BUTTON_KEYS[:-1])},
        {"columns": 2, "order": [*HOME_MENU_BUTTON_KEYS[:-1], "buy"]},
    ],
)
def test_home_menu_layout_rejects_hidden_or_invalid_buttons(value):
    with pytest.raises(SettingValidationError):
        validate_managed_setting("bot.home_layout.fa", value)
