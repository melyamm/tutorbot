from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import Message

from ..kb import btn, ikb
from ..util import item_from


async def begin(state: FSMContext, new_state: State, **data) -> None:
    await state.set_state(new_state)
    await state.set_data({"items": [], "groups": [], **data})


async def add(message: Message, state: FSMContext) -> None:
    item = item_from(message)
    if not item:
        await message.answer("Такое не сохраню. Пришли текст, фото или файл.")
        return
    data = await state.get_data()
    items = data.get("items", []) + [item]
    groups = data.get("groups", [])
    gid = message.media_group_id
    if gid and gid in groups:
        await state.update_data(items=items)
        return
    await state.update_data(items=items, groups=groups + [gid] if gid else groups)
    await message.answer(
        "📎 Принял. Можно добавить ещё или нажать «✅ Готово».",
        reply_markup=ikb([btn("✅ Готово", "col_done")]),
    )
