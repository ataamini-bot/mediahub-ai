from aiogram.fsm.state import State, StatesGroup


class PaymentStates(StatesGroup):
    confirming_offer = State()
    selecting_usdt_destination = State()
    waiting_for_receipt = State()


class AdminPaymentStates(StatesGroup):
    waiting_for_rejection_reason = State()
