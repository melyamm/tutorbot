from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import kb

router = Router(name="fallback")


@router.message()
async def any_message(message: Message, state: FSMContext, role: str | None):
    polite = role == "parent"
    if await state.get_state():
        await message.answer(
            "Не понял 🤔 Отправьте то, что просили, или /cancel чтобы отменить."
            if polite else "Не понял 🤔 Отправь то, что просили, или /cancel чтобы отменить."
        )
        return
    if role is None:
        await message.answer("Чтобы пользоваться ботом, перейди по персональной ссылке от репетитора.")
        return
    await message.answer(
        "Выберите действие в меню снизу 👇" if polite else "Выбери действие в меню снизу 👇",
        reply_markup=kb.menu_for(role),
    )


@router.callback_query()
async def any_callback(cq: CallbackQuery):
    await cq.answer("Эта кнопка больше не работает")
