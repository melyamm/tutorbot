import asyncio
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import db, kb, services as sv
from ..kb import CB, btn, ikb
from ..notify import drop_kb
from ..states import Restore
from ..util import h

router = Router(name="admin_restore")


@router.message(Command("restore"), StateFilter("*"))
async def restore_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Restore.file)
    await message.answer(
        "♻️ Восстановление базы.\n\nОтправь файл бэкапа (.db), который присылал бот. "
        "Текущая база будет заменена, но перед этим я пришлю её копию.\n\nПередумал: /cancel"
    )


@router.message(Restore.file, F.document)
async def restore_file(message: Message, state: FSMContext, bot: Bot):
    path = os.path.join(tempfile.gettempdir(), f"restore_{message.from_user.id}.db")
    await bot.download(message.document.file_id, destination=path)
    try:
        students = await asyncio.to_thread(db.inspect, path)
    except ValueError as e:
        os.remove(path)
        await message.answer(f"❌ Не подходит: {h(e)}. Пришли файл, который присылал бот по /backup.")
        return
    await state.update_data(path=path)
    await state.set_state(Restore.confirm)
    now = await db.val("SELECT COUNT(*) FROM students", default=0)
    await message.answer(
        f"В файле учеников: {students}. Сейчас в боте: {now}.\n\nЗаменить текущую базу файлом?",
        reply_markup=ikb([btn("Да, заменить", "rst_yes"), btn("Отмена", "rst_no")]),
    )


@router.message(Restore.file)
async def restore_not_file(message: Message):
    await message.answer("Нужен файл бэкапа (.db) документом. Передумал: /cancel")


@router.callback_query(Restore.confirm, CB.filter(F.a == "rst_no"))
async def restore_no(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    if data.get("path") and os.path.exists(data["path"]):
        os.remove(data["path"])
    await drop_kb(cq.message)
    await cq.message.answer("Отменено, база не тронута.", reply_markup=kb.ADMIN_MENU)
    await cq.answer()


@router.callback_query(Restore.confirm, CB.filter(F.a == "rst_yes"))
async def restore_yes(cq: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await cq.answer()
    await drop_kb(cq.message)
    path = data.get("path")
    if not path or not os.path.exists(path):
        await cq.message.answer("Файл потерялся, начни заново: /restore")
        return
    await sv.send_backup(bot, cq.message.chat.id, "💾 Копия базы ДО восстановления, на всякий случай.")
    await db.restore(path)
    os.remove(path)
    await sv.save_admins()
    sv.reset_tutor_link()
    await sv.ensure_lessons()
    students = await db.val("SELECT COUNT(*) FROM students WHERE active=1", default=0)
    await cq.message.answer(f"✅ База восстановлена. Активных учеников: {students}.", reply_markup=kb.ADMIN_MENU)
