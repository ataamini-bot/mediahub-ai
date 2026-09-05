from aiogram.fsm.state import State, StatesGroup


class AdminExperienceStates(StatesGroup):
    waiting_for_copy_value = State()

    waiting_for_home_label_fa = State()
    waiting_for_home_label_en = State()
    selecting_home_action = State()
    waiting_for_home_action_value = State()
    selecting_home_style = State()
    waiting_for_home_edit_value = State()

    waiting_for_channel_chat_id = State()
    waiting_for_channel_title = State()
    waiting_for_channel_invite_url = State()

