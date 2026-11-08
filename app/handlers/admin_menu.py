from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import kb, services as sv
from . import admin_hw, admin_lessons, admin_money, admin_students

router = Router(name="admin_menu")


@router.message(F.text == kb.A_STUDENTS, StateFilter("*"))
async def m_students(message: Message, state: FSMContext):
    await state.clear()
    text, markup = await admin_students.students_list()
    await message.answer(text, reply_markup=markup)


@router.message(F.text == kb.A_SCHED, StateFilter("*"))
async def m_schedule(message: Message, state: FSMContext):
    await state.clear()
    text, markup = await admin_lessons.schedule_view(0)
    await message.answer(text, reply_markup=markup)


@router.message(F.text == kb.A_HW, StateFilter("*"))
async def m_hw(message: Message, state: FSMContext):
    await state.clear()
    text, markup = await admin_hw.choose_student_view()
    await message.answer(text, reply_markup=markup)


@router.message(F.text == kb.A_REVIEW, StateFilter("*"))
async def m_review(message: Message, state: FSMContext):
    await state.clear()
    text, markup = await admin_hw.review_list()
    await message.answer(text, reply_markup=markup)


@router.message(F.text == kb.A_MONEY, StateFilter("*"))
async def m_money(message: Message, state: FSMContext):
    await state.clear()
    text, markup = await admin_money.finance_view()
    await message.answer(text, reply_markup=markup)


@router.message(Command("backup"), StateFilter("*"))
async def m_backup(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await sv.send_backup(
        bot, message.chat.id,
        "💾 Бэкап базы. Сохрани файл. Восстановить: /restore и отправь этот файл боту.",
    )


@router.message(F.text == kb.A_NEW, StateFilter("*"))
async def m_new(message: Message, state: FSMContext):
    await admin_students.start_new(message, state)
