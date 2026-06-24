from aiogram.fsm.state import State, StatesGroup


class GroupForm(StatesGroup):
    new_name = State()
    rename_name = State()


class OperatorForm(StatesGroup):
    add_operator = State()
    edit_remark = State()
    cleanup_time = State()


class SendForm(StatesGroup):
    wait_message = State()
    confirm = State()


class ReplyForm(StatesGroup):
    wait_message = State()


class ConfigForm(StatesGroup):
    replacement_text = State()
    replacement_photo = State()
