from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

A_STUDENTS = "👥 Ученики"
A_SCHED = "📅 Расписание"
A_HW = "📚 Выдать ДЗ"
A_REVIEW = "📥 На проверке"
A_MONEY = "💰 Финансы"
A_NEW = "➕ Новый ученик"
M_LESSONS = "📅 Занятия"
M_HW = "📚 Домашка"
M_PAY = "💳 Оплата"
DONE = "✅ Готово"
CANCEL = "✖️ Отмена"


class CB(CallbackData, prefix="c"):
    a: str
    i: int = 0
    v: str = ""


def btn(text: str, a: str, i: int = 0, v: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=CB(a=a, i=i, v=v).pack())


def btn_url(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def ikb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def _reply(rows, placeholder: str | None = None) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in r] for r in rows],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=placeholder,
    )


ADMIN_MENU = _reply([[A_STUDENTS, A_SCHED], [A_HW, A_REVIEW], [A_MONEY, A_NEW]])
STUDENT_MENU = _reply([[M_LESSONS, M_HW]])
PARENT_MENU = _reply([[M_LESSONS, M_PAY], [M_HW]])
COLLECT_KB = _reply([[DONE], [CANCEL]], "Текст, фото или файл")


def menu_for(role: str | None):
    return {"admin": ADMIN_MENU, "student": STUDENT_MENU, "parent": PARENT_MENU}.get(role, ReplyKeyboardRemove())


def dur_kb(a: str, i: int) -> InlineKeyboardMarkup:
    return ikb(
        [btn("45 мин", a, i, "45"), btn("60 мин", a, i, "60")],
        [btn("90 мин", a, i, "90"), btn("120 мин", a, i, "120")],
    )
