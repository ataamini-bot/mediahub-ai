from aiogram.fsm.state import State, StatesGroup


class AdminFinanceStates(StatesGroup):
    waiting_for_card_label = State()
    waiting_for_card_number = State()
    waiting_for_card_holder = State()
    waiting_for_card_bank = State()
    waiting_for_card_order = State()
    waiting_for_card_edit_value = State()

    waiting_for_usdt_label = State()
    waiting_for_usdt_network_name = State()
    waiting_for_usdt_network_code = State()
    waiting_for_usdt_address = State()
    waiting_for_usdt_confirmations = State()
    waiting_for_usdt_contract = State()
    waiting_for_usdt_explorer = State()
    waiting_for_usdt_order = State()
    waiting_for_usdt_edit_value = State()

    confirming_action = State()
