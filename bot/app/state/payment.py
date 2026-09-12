from aiogram.fsm.state import State, StatesGroup


class PaymentStates(StatesGroup):
    confirming_offer = State()
    selecting_usdt_destination = State()
    waiting_for_receipt = State()
    waiting_for_txid = State()
    waiting_for_usdt_screenshot = State()


class AdminPaymentStates(StatesGroup):
    confirming_approval = State()
    confirming_rejection = State()
    waiting_for_rejection_reason = State()
