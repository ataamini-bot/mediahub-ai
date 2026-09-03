from app.handlers.admin_finance import (
    _format_card_number,
    _normalize_card_number,
    _parse_integer,
)
from app.keyboards.admin_finance import (
    build_card_detail_keyboard,
    build_cards_keyboard,
    build_finance_home_keyboard,
    build_payment_list_keyboard,
    build_usdt_detail_keyboard,
)


def _callbacks(keyboard) -> list[str]:
    return [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_finance_home_separates_admin_and_public_payment_paths():
    keyboard = build_finance_home_keyboard(can_manage_destinations=True)
    callbacks = _callbacks(keyboard)

    assert "admin:pay:list:pending:1" in callbacks
    assert "admin:cards" in callbacks
    assert "admin:usdt" in callbacks
    assert "payment:open" not in callbacks


def test_card_crud_keyboard_has_edit_toggle_delete_and_add():
    card = {
        "id": 91,
        "label": "کارت اصلی",
        "card_number": "9999991234567890",
        "is_active": True,
    }
    list_callbacks = _callbacks(build_cards_keyboard([card]))
    detail_callbacks = _callbacks(build_card_detail_keyboard(card))

    assert "admin:card:add" in list_callbacks
    assert "admin:card:edit:number:91" in detail_callbacks
    assert "admin:card:toggle:91" in detail_callbacks
    assert "admin:card:delete:91" in detail_callbacks


def test_usdt_crud_keyboard_supports_all_operational_fields():
    destination = {"id": 45, "is_active": True}
    callbacks = _callbacks(build_usdt_detail_keyboard(destination))

    assert "admin:usdt:edit:network_code:45" in callbacks
    assert "admin:usdt:edit:address:45" in callbacks
    assert "admin:usdt:edit:confirmations_required:45" in callbacks
    assert "admin:usdt:edit:contract_address:45" in callbacks
    assert "admin:usdt:edit:explorer_url:45" in callbacks
    assert "admin:usdt:toggle:45" in callbacks
    assert "admin:usdt:delete:45" in callbacks


def test_payment_list_supports_pagination_and_arbitrary_ids():
    keyboard = build_payment_list_keyboard(
        [
            {
                "id": 9223372036854775807,
                "status": "pending",
                "user_telegram_id": 7777705023,
                "username": None,
                "amount": "125000.00",
            }
        ],
        status_filter="pending",
        page=2,
        total=25,
    )
    callbacks = _callbacks(keyboard)

    assert "admin:pay:list:pending:1" in callbacks
    assert "admin:pay:list:pending:3" in callbacks
    assert any(callback.startswith("admin:pay:view:9223372036854775807") for callback in callbacks)
    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)


def test_finance_numeric_normalization_accepts_persian_digits():
    assert _parse_integer("۱۲٬۵۰۰") == 12500
    assert _normalize_card_number("۹۹۹۹-۹۹۱۲-۳۴۵۶-۷۸۹۰") == "9999991234567890"
    assert _format_card_number("9999991234567890") == "9999-9912-3456-7890"
