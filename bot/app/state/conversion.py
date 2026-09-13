from aiogram.fsm.state import State, StatesGroup


class ConversionStates(StatesGroup):
    waiting_for_media = State()
    choosing_format = State()
