import secrets

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import db, kb, services as sv
from ..config import cfg
from ..kb import CB, btn, ikb
from ..notify import edit, send
from ..roles import VIEW_AS

router = Router(name="admin_transfer")

KEY = "admin_invite"


async def current_invite() -> tuple[str, str, int] | None:
    raw = await db.kv_get(KEY)
    if not raw:
        return None
    code, mode, creator = raw.split("|")
    return code, mode, int(creator)


@router.message(Command("transfer"), StateFilter("*"))
async def transfer_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👤 <b>Передача бота другому репетитору</b>\n\n"
        "Новый репетитор увидит всё: учеников, родителей, расписание, долги и домашки.\n\n"
        "Как передаём?",
        reply_markup=ikb(
            [btn("🔑 Передать полностью (я выйду)", "tr", 0, "full")],
            [btn("👥 Добавить вторым репетитором", "tr", 0, "add")],
            [btn("Отмена", "tr_no")],
        ),
    )


@router.callback_query(CB.filter(F.a == "tr_no"))
async def transfer_no(cq: CallbackQuery):
    await edit(cq.message, "Отменено, ничего не изменилось.")
    await cq.answer()


@router.callback_query(CB.filter(F.a == "tr"))
async def transfer_link(cq: CallbackQuery, callback_data: CB):
    mode = callback_data.v
    code = "a_" + secrets.token_urlsafe(12)
    await db.kv_set(KEY, f"{code}|{mode}|{cq.from_user.id}")
    link = f"https://t.me/{sv.BOT_USERNAME}?start={code}"
    if mode == "full":
        info = (
            "🔑 Ссылка готова. Как только новый репетитор по ней перейдёт, бот станет его, "
            "а ты перестанешь быть админом."
        )
    else:
        info = "👥 Ссылка готова. По ней новый репетитор станет вторым админом, ты останешься."
    await edit(
        cq.message,
        info + "\n\nСсылка одноразовая. Перешли ему сообщение ниже 👇",
        ikb([btn("❌ Отменить ссылку", "tr_cancel")]),
    )
    await cq.message.answer(
        f"Привет! Передаю тебе бота «Дневник занятий» с учениками. Перейди по ссылке, и ты станешь репетитором:\n\n{link}",
        disable_web_page_preview=True,
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "tr_cancel"))
async def transfer_cancel(cq: CallbackQuery):
    await db.run("DELETE FROM kv WHERE k=?", KEY)
    await edit(cq.message, "❌ Ссылка отменена, по ней больше никто не станет репетитором.")
    await cq.answer()


@router.message(Command("leave"), StateFilter("*"))
async def leave(message: Message, state: FSMContext):
    await state.clear()
    if len(cfg.admin_ids) < 2:
        await message.answer(
            "Ты единственный репетитор, выйти нельзя. Сначала добавь другого: /transfer"
        )
        return
    await message.answer(
        "Выйти из репетиторов? Бот останется у второго админа, ты перестанешь видеть учеников и финансы.",
        reply_markup=ikb([btn("Да, выйти", "leave_yes"), btn("Нет", "tr_no")]),
    )


@router.callback_query(CB.filter(F.a == "leave_yes"))
async def leave_yes(cq: CallbackQuery, bot: Bot):
    uid = cq.from_user.id
    if len(cfg.admin_ids) < 2 or uid not in cfg.admin_ids:
        await cq.answer("Выйти нельзя", show_alert=True)
        return
    await drop_admin(uid)
    await edit(cq.message, "Готово, ты больше не репетитор в этом боте.")
    await cq.message.answer("Меню убрано.", reply_markup=kb.menu_for(None))
    who = f"@{cq.from_user.username}" if cq.from_user.username else cq.from_user.full_name
    for other in cfg.admin_ids:
        await send(bot, other, f"ℹ️ {who} вышел из репетиторов. Теперь бот ведёшь ты.")
    await cq.answer()


async def drop_admin(uid: int) -> None:
    cfg.admin_ids.discard(uid)
    VIEW_AS.pop(uid, None)
    await sv.save_admins()
    sv.reset_tutor_link()


async def accept(message: Message, bot: Bot, code: str) -> None:
    u = message.from_user
    inv = await current_invite()
    if not inv or inv[0] != code:
        await message.answer("Эта ссылка недействительна или уже использована.")
        return
    _, mode, creator = inv
    if u.id in cfg.admin_ids:
        await message.answer("Ты уже репетитор в этом боте 🙂 Эту ссылку надо переслать другому человеку.")
        return
    await db.run("DELETE FROM kv WHERE k=?", KEY)
    cfg.admin_ids.add(u.id)
    await db.kv_set("tutor_contact", str(u.id))
    await sv.save_admins()
    sv.reset_tutor_link()
    who = f"@{u.username}" if u.username else u.full_name
    if mode == "full":
        await drop_admin(creator)
        await send(bot, 
            creator,
            f"🔑 Бот передан: {who} теперь репетитор. Ты больше не админ.",
            reply_markup=kb.menu_for(None),
        )
    else:
        for other in cfg.admin_ids - {u.id}:
            await send(bot, other, f"👥 {who} теперь второй репетитор в боте.")
    students = await db.val("SELECT COUNT(*) FROM students WHERE active=1", default=0)
    await message.answer(
        f"👋 Добро пожаловать! Теперь ты репетитор в этом боте. Учеников: {students}.\n\n"
        "Краткая инструкция: /help",
        reply_markup=kb.ADMIN_MENU,
    )
