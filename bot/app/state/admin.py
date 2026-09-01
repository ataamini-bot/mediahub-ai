from aiogram.fsm.state import State, StatesGroup


class AdminManagementStates(StatesGroup):
    waiting_for_admin_id = State()
    selecting_admin_roles = State()
    waiting_for_admin_reason = State()
    confirming_admin_change = State()
    confirming_dangerous_admin_change = State()

    waiting_for_role_code = State()
    waiting_for_role_name = State()
    waiting_for_role_description = State()
    selecting_role_permissions = State()
    waiting_for_role_reason = State()
    confirming_role_change = State()
