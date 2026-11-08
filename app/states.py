from aiogram.fsm.state import State, StatesGroup


class AddStudent(StatesGroup):
    name = State()
    grade = State()
    subject = State()
    rate = State()


class EditField(StatesGroup):
    value = State()


class SlotAdd(StatesGroup):
    time = State()
    duration = State()


class OneOff(StatesGroup):
    when = State()
    duration = State()


class Move(StatesGroup):
    when = State()


class HwAssign(StatesGroup):
    collect = State()
    deadline = State()


class Review(StatesGroup):
    comment = State()


class AdminPay(StatesGroup):
    amount = State()


class PayFix(StatesGroup):
    amount = State()


class HwSubmit(StatesGroup):
    collect = State()


class Restore(StatesGroup):
    file = State()
    confirm = State()


class ParentPay(StatesGroup):
    amount = State()
    receipt = State()
