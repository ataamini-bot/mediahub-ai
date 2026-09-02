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

    waiting_for_plan_name = State()
    waiting_for_plan_duration = State()
    waiting_for_plan_price = State()
    waiting_for_plan_daily_limit = State()
    waiting_for_plan_file_size = State()
    selecting_plan_quality = State()
    selecting_plan_concurrency = State()
    selecting_plan_priority = State()
    selecting_plan_forced_join = State()
    waiting_for_plan_description = State()
    confirming_plan_create = State()

    waiting_for_plan_edit_value = State()
    confirming_plan_update = State()
