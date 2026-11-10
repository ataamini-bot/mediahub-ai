from aiogram.fsm.state import State, StatesGroup


class AdminSettingStates(StatesGroup):
    waiting_for_value = State()
    confirming_change = State()
