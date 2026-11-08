from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import db, kb, services as sv, timeutil as tu
from ..config import cfg
from ..kb import CB, btn, btn_url, ikb
from ..notify import drop_kb, to_admins
from ..roles import Role
from ..states import HwSubmit, ParentPay
from ..util import h, parse_int, rub
from . import collect

router = Router(name="member")
router.message.filter(Role("student", "parent"))


def _q(n: int) -> str:
    return ",".join("?" * n)


async def lessons_view(ms, role: str):
    sids = [m["student_id"] for m in ms]
    now = tu.now_utc()
    rows = await db.rows(
        sv.LESSON_SQL + f" WHERE l.student_id IN ({_q(len(sids))}) AND l.status='planned' "
        "AND l.start_utc>? AND l.start_utc<=? ORDER BY l.start_utc",
        *sids, tu.iso(now), tu.iso(now + timedelta(days=14)),
    )
    if not rows:
        return "Ближайших занятий нет.", None
    multi = len(sids) > 1
    lines = ["📅 <b>Ближайшие занятия</b>", ""]
    buttons = []
    for r in rows:
        stz = tu.zone(r["tz"])
        start = tu.parse_iso(r["start_utc"])
        prefix = f"{h(r['name'])}: " if multi else ""
        lines.append(f"• {prefix}{tu.fmt_dt(start, stz)} ({r['duration']} мин)")
        label = ("🙅 Не смогу: " if role == "student" else "🙅 Не получится: ") + tu.fmt_short(start, stz)
        if multi:
            label += f" ({r['name']})"
        buttons.append([btn(label[:60], "mc", r["id"])])
    if role == "student":
        lines.append("\nЕсли не получается прийти, нажми кнопку ниже и напиши репетитору, договоритесь о переносе.")
    else:
        lines.append("\nЕсли занятие не получится, нажмите кнопку ниже и напишите репетитору, договоритесь о переносе.")
    return "\n".join(lines), ikb(*buttons[:10])


@router.message(F.text == kb.M_LESSONS, StateFilter("*"))
async def m_lessons(message: Message, state: FSMContext, ms: list, role: str):
    await state.clear()
    text, markup = await lessons_view(ms, role)
    await message.answer(text, reply_markup=markup)


@router.callback_query(CB.filter(F.a == "mc"))
async def mc(cq: CallbackQuery, callback_data: CB, bot: Bot):
    uid = cq.from_user.id
    l = await sv.lesson(callback_data.i)
    role = await sv.member_role(uid, l["student_id"]) if l else None
    if not role:
        await cq.answer("Нет доступа", show_alert=True)
        return
    start = tu.parse_iso(l["start_utc"])
    if l["status"] != "planned" or start <= tu.now_utc():
        await cq.answer("Это занятие уже прошло или отменено", show_alert=True)
        return
    await cq.answer()
    link = await sv.tutor_link(bot)
    markup = ikb([btn_url("✉️ Написать репетитору", link)]) if link else None
    when = tu.fmt_dt(start, tu.zone(l["tz"]))
    if role == "student":
        text = f"Занятие {when}: напиши репетитору, договоритесь о переносе 👇"
    else:
        text = f"Занятие {when}: напишите, пожалуйста, репетитору, договоритесь о переносе 👇"
    if not link:
        text = text.replace(" 👇", ". Репетитор уже получил уведомление.")
    await cq.message.answer(text, reply_markup=markup)
    key = f"cant:{l['id']}:{uid}"
    if await db.kv_get(key):
        return
    await db.kv_set(key, "1")
    m = await db.one("SELECT * FROM members WHERE tg_id=? AND student_id=? AND role=?", uid, l["student_id"], role)
    by = ("Ученик" if role == "student" else "Родитель") + f" {sv.who(m)}"
    await to_admins(
        bot,
        f"🙅 {h(by)}: <b>{h(l['name'])}</b> не сможет прийти {tu.fmt_dt(start, cfg.tz)}.",
        reply_markup=ikb([btn("🔁 Перенести", "ls_move", l["id"]), btn("❌ Отменить", "ls_cancel", l["id"])]),
    )


async def student_hw_view(ms):
    sids = [m["student_id"] for m in ms]
    since = tu.iso(tu.now_utc() - timedelta(days=14))
    rows = await db.rows(
        sv.HW_SQL + f" WHERE hw.student_id IN ({_q(len(sids))}) AND (hw.status IN ('assigned','redo','submitted') "
        "OR (hw.status='accepted' AND hw.checked_at>?)) ORDER BY "
        "CASE hw.status WHEN 'redo' THEN 0 WHEN 'assigned' THEN 1 WHEN 'submitted' THEN 2 ELSE 3 END, hw.deadline_utc",
        *sids, since,
    )
    if not rows:
        return "Домашек нет 🎉", None
    lines = ["📚 <b>Домашка</b>", ""]
    buttons = []
    for r in rows:
        st = sv.hw_state(r)
        dl = f", срок {sv.deadline_text(r, tu.zone(r['tz']))}" if r["deadline_utc"] and st != "accepted" else ""
        lines.append(f"{sv.HW_ICON[st]} {h(r['title'])}: {sv.HW_RU[st]}{dl}")
        buttons.append([btn(f"{sv.HW_ICON[st]} {r['title']}"[:60], "sh", r["id"])])
    return "\n".join(lines), ikb(*buttons[:15])


async def parent_hw_view(ms):
    lines = ["📚 <b>Домашние задания</b>"]
    for m in ms:
        rows = await db.rows(sv.HW_SQL + " WHERE hw.student_id=? ORDER BY hw.id DESC LIMIT 8", m["student_id"])
        lines.append(f"\n<b>{h(m['name'])}</b>")
        if not rows:
            lines.append("Заданий пока не было.")
        for r in rows:
            st = sv.hw_state(r)
            dl = f" (срок {sv.deadline_text(r, tu.zone(r['tz']))})" if r["deadline_utc"] and st != "accepted" else ""
            lines.append(f"{sv.HW_ICON[st]} {h(r['title'])}: {sv.HW_RU[st]}{dl}")
    return "\n".join(lines)


@router.message(F.text == kb.M_HW, StateFilter("*"))
async def m_hw(message: Message, state: FSMContext, ms: list, role: str):
    await state.clear()
    if role == "student":
        text, markup = await student_hw_view(ms)
        await message.answer(text, reply_markup=markup)
    else:
        await message.answer(await parent_hw_view(ms))


async def _own_hw(cq: CallbackQuery, hid: int):
    row = await sv.hw(hid)
    if not row or not await sv.member_role(cq.from_user.id, row["student_id"]):
        await cq.answer("Нет доступа", show_alert=True)
        return None
    return row


@router.callback_query(CB.filter(F.a == "sh"))
async def sh(cq: CallbackQuery, callback_data: CB):
    row = await _own_hw(cq, callback_data.i)
    if not row:
        return
    st = sv.hw_state(row)
    lines = [
        f"{sv.HW_ICON[st]} <b>{h(row['title'])}</b>",
        f"Статус: {sv.HW_RU[st]}",
        f"Срок: {sv.deadline_text(row, tu.zone(row['tz']))}",
    ]
    if row["comment"] and row["status"] in ("redo", "accepted"):
        lines.append(f"\n💬 {row['comment']}")
    buttons = [btn("📎 Задание", "hst", row["id"])]
    if row["status"] in ("assigned", "redo"):
        buttons.append(btn("📤 Сдать", "hws", row["id"]))
    await cq.message.answer("\n".join(lines), reply_markup=ikb(buttons))
    await cq.answer()


@router.callback_query(CB.filter(F.a == "hst"))
async def hst(cq: CallbackQuery, callback_data: CB, bot: Bot):
    row = await _own_hw(cq, callback_data.i)
    if not row:
        return
    await cq.answer()
    await sv.show_task(bot, cq.message.chat.id, row["id"])


@router.callback_query(CB.filter(F.a == "hws"))
async def hws(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    row = await _own_hw(cq, callback_data.i)
    if not row:
        return
    if row["status"] not in ("assigned", "redo"):
        await cq.answer("Это ДЗ уже сдано" if row["status"] == "submitted" else "Это ДЗ уже принято", show_alert=True)
        return
    await collect.begin(state, HwSubmit.collect, hid=row["id"])
    await cq.message.answer(
        f"📤 Сдаём «{h(row['title'])}».\n\nПришли фото или файлы решения, можно несколько и альбомом. "
        "Когда всё, жми «✅ Готово».",
        reply_markup=kb.COLLECT_KB,
    )
    await cq.answer()


@router.message(HwSubmit.collect, F.text == kb.DONE)
async def hw_submit_done(message: Message, state: FSMContext, bot: Bot, role: str):
    data = await state.get_data()
    items = data.get("items", [])
    if not items:
        await message.answer("Сначала пришли решение: фото или файл.")
        return
    await state.clear()
    row = await sv.hw(data["hid"])
    if not row or row["status"] not in ("assigned", "redo"):
        await message.answer("Это ДЗ уже нельзя сдать.", reply_markup=kb.menu_for(role))
        return
    rnd = row["round"] + 1
    now = tu.iso(tu.now_utc())
    await db.run_many(
        "INSERT INTO hw_items(hw_id, kind, round, type, file_id, text, created_at) VALUES(?,?,?,?,?,?,?)",
        [(row["id"], "solution", rnd, it["type"], it.get("file_id"), it.get("text"), now) for it in items],
    )
    await db.run(
        "UPDATE homework SET status='submitted', round=?, submitted_at=? WHERE id=?", rnd, now, row["id"]
    )
    await message.answer("📤 Отправлено репетитору на проверку ✅", reply_markup=kb.menu_for(role))
    for uid in cfg.admin_ids:
        await sv.send_review(bot, uid, row["id"])


@router.callback_query(HwSubmit.collect, CB.filter(F.a == "col_done"))
async def hw_submit_done_btn(cq: CallbackQuery, state: FSMContext, bot: Bot, role: str | None):
    await cq.answer()
    await drop_kb(cq.message)
    await hw_submit_done(cq.message, state, bot, role)


@router.message(HwSubmit.collect)
async def hw_submit_collect(message: Message, state: FSMContext):
    await collect.add(message, state)


async def pay_view(ms):
    lines = ["💳 <b>Оплата</b>"]
    buttons = []
    multi = len(ms) > 1
    for m in ms:
        sid = m["student_id"]
        bal = await sv.balance(sid)
        lines.append(f"\n<b>{h(m['name'])}</b>\n{sv.balance_line_parent(bal)}")
        ops = await db.rows("SELECT * FROM ledger WHERE student_id=? ORDER BY id DESC LIMIT 4", sid)
        for r in ops:
            d = tu.parse_iso(r["created_at"]).astimezone(tu.zone(m["tz"]))
            sign = "+" if r["amount"] > 0 else "-"
            lines.append(f"  {d:%d.%m} · {h(r['note'] or r['kind'])} · {sign}{rub(abs(r['amount']))}")
        buttons.append([btn("✅ Я оплатил(а)" + (f": {m['name']}" if multi else ""), "pay_start", sid)])
    lines.append("\nОплата по факту после занятий. После перевода нажмите кнопку и пришлите чек, репетитор подтвердит.")
    if cfg.pay_info:
        lines.append(f"\n{h(cfg.pay_info)}")
    return "\n".join(lines), ikb(*buttons)


@router.message(F.text == kb.M_PAY, StateFilter("*"))
async def m_pay(message: Message, state: FSMContext, ms: list):
    await state.clear()
    text, markup = await pay_view(ms)
    await message.answer(text, reply_markup=markup)


@router.callback_query(CB.filter(F.a == "pay_start"))
async def pay_start(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    sid = callback_data.i
    if not await sv.member_role(cq.from_user.id, sid):
        await cq.answer("Нет доступа", show_alert=True)
        return
    debt = await sv.balance(sid)
    await state.set_state(ParentPay.amount)
    await state.set_data({"sid": sid})
    markup = ikb([btn(rub(debt), "pp_amt", sid, str(debt))]) if debt > 0 else None
    await cq.message.answer("Какую сумму вы перевели? Напишите числом или нажмите кнопку.", reply_markup=markup)
    await cq.answer()


async def _ask_receipt(msg: Message, state: FSMContext, amount: int) -> None:
    await state.update_data(amount=amount)
    await state.set_state(ParentPay.receipt)
    await msg.answer(
        f"Сумма: {rub(amount)}. Пришлите, пожалуйста, скриншот или чек (фото или файл). "
        "Если чека нет, нажмите кнопку.",
        reply_markup=ikb([btn("Без чека", "pp_nor")]),
    )


@router.message(ParentPay.amount, F.text)
async def pay_amount(message: Message, state: FSMContext):
    amount = parse_int(message.text)
    if not amount:
        await message.answer("Напишите сумму числом, например 3000")
        return
    await _ask_receipt(message, state, amount)


@router.callback_query(ParentPay.amount, CB.filter(F.a == "pp_amt"))
async def pay_amount_btn(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await cq.answer()
    await drop_kb(cq.message)
    await _ask_receipt(cq.message, state, int(callback_data.v))


async def _create_request(bot: Bot, msg: Message, state: FSMContext, tg_id: int, file_id, file_type) -> None:
    data = await state.get_data()
    await state.clear()
    pid = await db.run(
        "INSERT INTO pay_requests(student_id, tg_id, amount, file_id, file_type, created_at) VALUES(?,?,?,?,?,?)",
        data["sid"], tg_id, data["amount"], file_id, file_type, tu.iso(tu.now_utc()),
    )
    await msg.answer("Спасибо! Отправил репетитору на подтверждение, придёт уведомление.")
    for uid in cfg.admin_ids:
        await sv.send_pay_request(bot, uid, pid)


@router.message(ParentPay.receipt, F.photo)
async def pay_receipt_photo(message: Message, state: FSMContext, bot: Bot):
    await _create_request(bot, message, state, message.from_user.id, message.photo[-1].file_id, "photo")


@router.message(ParentPay.receipt, F.document)
async def pay_receipt_doc(message: Message, state: FSMContext, bot: Bot):
    await _create_request(bot, message, state, message.from_user.id, message.document.file_id, "document")


@router.callback_query(ParentPay.receipt, CB.filter(F.a == "pp_nor"))
async def pay_no_receipt(cq: CallbackQuery, state: FSMContext, bot: Bot):
    await cq.answer()
    await drop_kb(cq.message)
    await _create_request(bot, cq.message, state, cq.from_user.id, None, None)


@router.message(ParentPay.receipt)
async def pay_receipt_other(message: Message):
    await message.answer("Пришлите фото или файл чека, или нажмите «Без чека» выше.")
