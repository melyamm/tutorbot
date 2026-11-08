from datetime import datetime, time, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import db, services as sv, timeutil as tu
from ..config import cfg
from ..kb import CB, btn, ikb
from ..notify import edit, to_members
from ..states import Move
from ..util import h, rub

router = Router(name="admin_lessons")


async def schedule_view(offset: int = 0):
    tz = cfg.tz
    today = tu.now_utc().astimezone(tz).date()
    d0 = today + timedelta(days=7 * offset)
    start = datetime.combine(d0, time(0), tzinfo=tz)
    end = datetime.combine(d0 + timedelta(days=7), time(0), tzinfo=tz)
    rows = await db.rows(
        sv.LESSON_SQL + " WHERE l.start_utc>=? AND l.start_utc<? ORDER BY l.start_utc", tu.iso(start), tu.iso(end)
    )
    title = "📅 <b>Ближайшие 7 дней</b>" if offset == 0 else f"📅 <b>{d0:%d.%m}-{d0 + timedelta(days=6):%d.%m}</b>"
    lines = [title]
    now = tu.now_utc()
    cur_day = None
    buttons, row = [], []
    for r in rows:
        st = tu.parse_iso(r["start_utc"])
        day = st.astimezone(tz).date()
        if day != cur_day:
            lines.append(f"\n<b>{tu.fmt_day(st, tz)}</b>")
            cur_day = day
        icon = sv.STATUS_ICON[r["status"]]
        if r["status"] == "planned" and st > now:
            icon = "•"
        lines.append(f"{icon} {tu.fmt_time(st, tz)} {h(r['name'])} ({r['duration']} мин)")
        row.append(btn(f"{tu.WD[st.astimezone(tz).weekday()]} {tu.fmt_time(st, tz)} {r['name'][:14]}", "ls", r["id"]))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    if not rows:
        lines.append("\nЗанятий нет.")
    else:
        lines.append("\n• запланировано, ✅ проведено, ❌ отменено")
    buttons = buttons[:45]
    buttons.append([btn("« Неделя назад", "sched", offset - 1), btn("Неделя вперёд »", "sched", offset + 1)])
    return "\n".join(lines), ikb(*buttons)


async def lesson_view(lid: int):
    l = await sv.lesson(lid)
    if not l:
        return "Занятие не найдено.", None
    start = tu.parse_iso(l["start_utc"])
    future = start > tu.now_utc()
    lines = [
        f"<b>{h(l['name'])}</b>",
        f"{tu.fmt_dt(start, cfg.tz)}, {l['duration']} мин",
        f"Статус: {sv.STATUS_RU[l['status']]}",
    ]
    if l["status"] == "done" and l["price"]:
        lines.append(f"Начислено: {rub(l['price'])}")
    rows = []
    st = l["status"]
    if st == "planned" and future:
        rows.append([btn("🔁 Перенести", "ls_move", lid), btn("❌ Отменить", "ls_cancel", lid)])
    elif st == "planned":
        rows.append([btn("✅ Проведено", "ls_done", lid), btn("❌ Не было", "ls_cancel", lid)])
    elif st == "done":
        rows.append([btn("↩️ Не было (вернуть деньги)", "ls_undo", lid)])
    elif st == "cancelled":
        if future:
            rows.append([btn("♻️ Вернуть в расписание", "ls_restore", lid)])
        rows.append([btn("✅ Всё-таки было", "ls_done", lid)] if not future else [])
    rows.append([btn("👤 Ученик", "st", l["student_id"]), btn("« Расписание", "sched", 0)])
    return "\n".join(lines), ikb(*rows)


async def _show(cq: CallbackQuery, lid: int, note: str = "") -> None:
    text, markup = await lesson_view(lid)
    await edit(cq.message, (note + "\n\n" if note else "") + text, markup)


@router.callback_query(CB.filter(F.a == "sched"))
async def cb_sched(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await state.clear()
    text, markup = await schedule_view(callback_data.i)
    await edit(cq.message, text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "ls"))
async def cb_lesson(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await state.clear()
    await _show(cq, callback_data.i)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "ls_cancel"))
async def cb_cancel(cq: CallbackQuery, callback_data: CB, bot: Bot):
    lid = callback_data.i
    l = await sv.lesson(lid)
    if not l or l["status"] not in ("planned", "done"):
        await cq.answer("Нельзя отменить", show_alert=True)
        return
    was_billed = l["status"] == "done" and l["billed"]
    refunded = await sv.refund(lid)
    await db.run("UPDATE lessons SET status='cancelled', billed=1 WHERE id=?", lid)
    start = tu.parse_iso(l["start_utc"])
    stz = tu.zone(l["tz"])
    if start > tu.now_utc():
        await to_members(bot, l["student_id"], "student", f"❌ Занятие {tu.fmt_dt(start, stz)} отменено репетитором.")
        await to_members(
            bot, l["student_id"], "parent", f"❌ Занятие <b>{h(l['name'])}</b> {tu.fmt_dt(start, stz)} отменено репетитором."
        )
    elif was_billed and refunded:
        await to_members(
            bot, l["student_id"], "parent",
            f"↩️ Начисление {rub(refunded)} за {tu.fmt_dt(start, stz)} отменено: занятия не было.",
        )
    await _show(cq, lid, "❌ Отменено.")
    await cq.answer()


@router.callback_query(CB.filter(F.a == "ls_undo"))
async def cb_undo(cq: CallbackQuery, callback_data: CB, bot: Bot):
    lid = callback_data.i
    l = await sv.lesson(lid)
    if not l or l["status"] != "done":
        await cq.answer("Уже изменено", show_alert=True)
        return
    refunded = await sv.refund(lid)
    await db.run("UPDATE lessons SET status='cancelled' WHERE id=?", lid)
    if l["billed"] and refunded:
        start = tu.parse_iso(l["start_utc"])
        await to_members(
            bot, l["student_id"], "parent",
            f"↩️ Начисление {rub(refunded)} за {tu.fmt_dt(start, tu.zone(l['tz']))} отменено: занятия не было.",
        )
    await db.run("UPDATE lessons SET billed=1 WHERE id=?", lid)
    await _show(cq, lid, f"↩️ Отмечено, что занятия не было. Возвращено {rub(refunded)}.")
    await cq.answer()


@router.callback_query(CB.filter(F.a == "ls_done"))
async def cb_done(cq: CallbackQuery, callback_data: CB):
    lid = callback_data.i
    l = await sv.lesson(lid)
    if not l or l["status"] == "done":
        await cq.answer("Уже проведено")
        return
    if tu.parse_iso(l["start_utc"]) > tu.now_utc():
        await cq.answer("Занятие ещё не началось", show_alert=True)
        return
    price = await sv.mark_done(lid)
    await _show(cq, lid, f"✅ Проведено, начислено {rub(price)}.")
    await cq.answer()


@router.callback_query(CB.filter(F.a == "ls_restore"))
async def cb_restore(cq: CallbackQuery, callback_data: CB, bot: Bot):
    lid = callback_data.i
    l = await sv.lesson(lid)
    start = tu.parse_iso(l["start_utc"]) if l else None
    if not l or l["status"] != "cancelled" or start <= tu.now_utc():
        await cq.answer("Нельзя вернуть", show_alert=True)
        return
    await db.run("UPDATE lessons SET status='planned', remind_day=0, remind_hour=0, billed=0 WHERE id=?", lid)
    stz = tu.zone(l["tz"])
    await to_members(bot, l["student_id"], "student", f"♻️ Занятие {tu.fmt_dt(start, stz)} снова в расписании.")
    await to_members(
        bot, l["student_id"], "parent", f"♻️ Занятие <b>{h(l['name'])}</b> {tu.fmt_dt(start, stz)} снова в расписании."
    )
    await _show(cq, lid, "♻️ Вернул в расписание.")
    await cq.answer()


@router.callback_query(CB.filter(F.a == "ls_move"))
async def cb_move(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await state.set_state(Move.when)
    await state.set_data({"lid": callback_data.i})
    await cq.message.answer(
        f"Новые дата и время ({tu.tz_label(cfg.tz.key)})? Например: 15.11 18:00 или завтра 17:30"
    )
    await cq.answer()


@router.message(Move.when, F.text)
async def move_when(message: Message, state: FSMContext, bot: Bot):
    when = tu.parse_dt(message.text, cfg.tz)
    if not when:
        await message.answer("Не понял. Пример: 15.11 18:00")
        return
    if when <= tu.now_utc():
        await message.answer("Это время уже прошло.")
        return
    data = await state.get_data()
    await state.clear()
    l = await sv.lesson(data["lid"])
    if not l or l["status"] not in ("planned", "cancelled"):
        await message.answer("Это занятие уже нельзя перенести.")
        return
    old = tu.parse_iso(l["start_utc"])
    await db.run(
        "UPDATE lessons SET start_utc=?, status='planned', remind_day=0, remind_hour=0, billed=0 WHERE id=?",
        tu.iso(when), l["id"],
    )
    stz = tu.zone(l["tz"])
    info = f"с {tu.fmt_dt(old, stz)} на <b>{tu.fmt_dt(when, stz)}</b>"
    await to_members(bot, l["student_id"], "student", f"🔁 Занятие перенесено {info}.")
    await to_members(bot, l["student_id"], "parent", f"🔁 Занятие <b>{h(l['name'])}</b> перенесено {info}.")
    text, markup = await lesson_view(l["id"])
    await message.answer("🔁 Перенесено.\n\n" + text, reply_markup=markup)
