from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import db, kb, timeutil as tu
from ..config import cfg
from ..notify import to_admins
from ..roles import VIEW_AS, memberships, resolve
from ..util import h

router = Router(name="common")

ROLE_RU = {"student": "ученик", "parent": "родитель"}
MODE_RU = {"admin": "репетитор", "student": "ученик", "parent": "родитель"}

STUDENT_GUIDE = (
    "Здесь будут напоминания о занятиях и домашка.\n\n"
    "📤 Сдать ДЗ: кнопка под заданием, фото решения, «✅ Готово».\n"
    "🙅 Не можешь прийти: «📅 Занятия» → «Не смогу».\n\n"
    "Меню снизу 👇"
)

HELP = {
    "admin": (
        "<b>Как пользоваться</b>\n\n"
        "1. «➕ Новый ученик»: имя, класс, предмет, ставка.\n"
        "2. В карточке ученика: «🕒 Расписание» задаёт регулярные занятия, «🔗 Ссылка ученику/родителю» даёт "
        "одноразовые ссылки для подключения.\n"
        "3. Бот сам напоминает о занятиях за день и за час, после занятия отмечает его проведённым и "
        "начисляет оплату. Если занятия не было, жми «↩️ Не было» в уведомлении.\n"
        "4. «📚 Выдать ДЗ»: присылаешь материалы, выбираешь срок. Решения приходят в «📥 На проверке».\n"
        "5. «💰 Финансы»: долги, доход за месяц, заявки на оплату от родителей.\n\n"
        "/mode переключает вид: репетитор, ученик, родитель (если ты сам подключён по ссылке).\n"
        "/unlink отвязывает тебя от тестовых подключений.\n"
        "/cancel прерывает любое действие.\n"
        "/backup присылает файл базы. Автоматически бэкап приходит каждое воскресенье вечером.\n"
        "/restore загружает базу из файла бэкапа (например, при переезде на другой сервер).\n"
        "/transfer передаёт бота другому репетитору или добавляет второго.\n"
        "/leave выйти из репетиторов, если в боте есть второй."
    ),
    "student": None,
    "parent": (
        "Здесь напоминания о занятиях, статус домашних заданий и оплата.\n\n"
        "«📅 Занятия»: расписание и отмена.\n«💳 Оплата»: долг и подтверждение перевода.\n«📚 Домашка»: что задано и что сдано."
    ),
}
HELP["student"] = STUDENT_GUIDE


@router.message(CommandStart(deep_link=True), StateFilter("*"))
async def start_invite(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    u = message.from_user
    code = (command.args or "").strip()
    if code.startswith("a_"):
        from .admin_transfer import accept

        await accept(message, bot, code)
        return
    inv = await db.one("SELECT * FROM invites WHERE code=?", code)
    st = await db.one("SELECT * FROM students WHERE id=? AND active=1", inv["student_id"]) if inv else None
    if not inv or inv["used_by"] or not st:
        role, _ = await resolve(u.id)
        await message.answer(
            "Эта ссылка недействительна или уже использована. Попроси у репетитора новую.",
            reply_markup=kb.menu_for(role),
        )
        return
    now = tu.iso(tu.now_utc())
    await db.run(
        "INSERT OR IGNORE INTO members(student_id, tg_id, role, full_name, username, created_at) VALUES(?,?,?,?,?,?)",
        st["id"], u.id, inv["role"], u.full_name, u.username, now,
    )
    await db.run("UPDATE invites SET used_by=?, used_at=? WHERE code=?", u.id, now, code)
    role, _ = await resolve(u.id)
    if inv["role"] == "student":
        text = f"Привет, {h(st['name'])}! 👋\n\n{STUDENT_GUIDE}"
    else:
        text = (
            f"Здравствуйте! 👋\n\nВы подключены как родитель ученика <b>{h(st['name'])}</b>. "
            "Сюда будут приходить напоминания о занятиях, статус домашних заданий и информация об оплате."
        )
    if u.id in cfg.admin_ids:
        text += "\n\nТы админ, поэтому меню осталось админским. Посмотреть бота глазами ученика или родителя: /mode"
    await message.answer(text, reply_markup=kb.menu_for(role))
    who = f"@{u.username}" if u.username else u.full_name
    if inv["role"] == "student":
        note = f"🔗 Ученик <b>{h(st['name'])}</b> подключился к боту ({h(who)})"
    else:
        note = f"🔗 Родитель ученика <b>{h(st['name'])}</b> подключён к боту ({h(who)})"
    await to_admins(bot, note)


@router.message(CommandStart(), StateFilter("*"))
async def start(message: Message, state: FSMContext, role: str | None):
    await state.clear()
    if not cfg.admin_ids:
        uid = message.from_user.id
        cfg.admin_ids.add(uid)
        await db.kv_set("admin_ids", str(uid))
        await message.answer(
            "👋 Ты первый, кто написал этому боту, поэтому теперь ты его репетитор (админ). "
            "Больше никто админом стать не сможет.\n\nКраткая инструкция: /help",
            reply_markup=kb.ADMIN_MENU,
        )
        return
    texts = {
        "admin": "Привет! Это панель репетитора. Меню снизу 👇\nКраткая инструкция: /help",
        "student": "Привет! Меню снизу 👇",
        "parent": "Здравствуйте! Меню снизу 👇",
    }
    text = texts.get(role, "Привет! Это бот репетитора. Чтобы подключиться, перейди по персональной ссылке от репетитора.")
    await message.answer(text, reply_markup=kb.menu_for(role))


@router.message(Command("help"), StateFilter("*"))
async def help_cmd(message: Message, role: str | None):
    await message.answer(HELP.get(role, "Чтобы пользоваться ботом, перейди по ссылке от репетитора."), reply_markup=kb.menu_for(role))


@router.message(Command("id"), StateFilter("*"))
async def my_id(message: Message):
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


@router.message(F.text == kb.CANCEL, StateFilter("*"))
@router.message(Command("cancel"), StateFilter("*"))
async def cancel(message: Message, state: FSMContext, role: str | None):
    await state.clear()
    await message.answer("Отменено.", reply_markup=kb.menu_for(role))


@router.message(Command("mode"), StateFilter("*"))
async def mode(message: Message, state: FSMContext):
    uid = message.from_user.id
    if uid not in cfg.admin_ids:
        return
    await state.clear()
    roles = sorted({m["role"] for m in await memberships(uid)})
    if not roles:
        await message.answer(
            "Чтобы посмотреть бота глазами ученика или родителя, открой карточку любого ученика, "
            "нажми «🔗 Ссылка ученику» и перейди по ней сам. Потом снова /mode.\n\n"
            "Только не забудь потом /unlink и выдать ученику новую ссылку."
        )
        return
    cycle = [None] + roles
    cur = VIEW_AS.get(uid)
    nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else cycle[1]
    if nxt:
        VIEW_AS[uid] = nxt
    else:
        VIEW_AS.pop(uid, None)
    role, _ = await resolve(uid)
    await message.answer(
        f"Режим: <b>{MODE_RU[role]}</b>. Переключить дальше: /mode\n"
        "Кнопки в уведомлениях репетитора работают в любом режиме.",
        reply_markup=kb.menu_for(role),
    )


@router.message(Command("unlink"), StateFilter("*"))
async def unlink(message: Message, state: FSMContext):
    uid = message.from_user.id
    if uid not in cfg.admin_ids:
        return
    await state.clear()
    await db.run("DELETE FROM members WHERE tg_id=?", uid)
    VIEW_AS.pop(uid, None)
    await message.answer("Готово, ты отвязан от всех учеников. Режим: репетитор.", reply_markup=kb.ADMIN_MENU)
