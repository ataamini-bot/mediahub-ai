from aiogram.fsm.state import State, StatesGroup


class PaymentStates(StatesGroup):
    waiting_for_receipt = State()


class AdminPaymentStates(StatesGroup):
    waiting_for_rejection_reason = State()
