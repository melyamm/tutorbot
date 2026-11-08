from datetime import datetime, time, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import db, kb, services as sv, timeutil as tu
from ..config import cfg
from ..kb import CB, btn, ikb
from ..notify import drop_kb, edit, send, send_items, to_members
from ..states import HwAssign, Review
from ..util import h, make_title
from . import collect

router = Router(name="admin_hw")


async def choose_student_view():
    rows = await sv.active_students()
    if not rows:
        return "Сначала добавь ученика.", None
    return "Кому выдать ДЗ?", ikb(*[[btn(s["name"], "hw_for", s["id"])] for s in rows])


async def review_list():
    rows = await db.rows(sv.HW_SQL + " WHERE hw.status='submitted' ORDER BY hw.submitted_at")
    if not rows:
        return "Нечего проверять 👌", None
    return f"📥 <b>На проверке</b>: {len(rows)}", ikb(
        *[[btn(f"{r['name']}: {r['title']}"[:60], "rv", r["id"])] for r in rows]
    )


async def deadline_kb(sid: int):
    rows = []
    nl = await sv.next_lesson(sid)
    if nl:
        rows.append([btn(f"К занятию: {tu.fmt_short(tu.parse_iso(nl['start_utc']), cfg.tz)}", "dl", 0, "next")])
    rows.append([btn("Через 2 дня", "dl", 0, "2"), btn("Через неделю", "dl", 0, "7")])
    rows.append([btn("Без срока", "dl", 0, "none")])
    return ikb(*rows)


@router.callback_query(CB.filter(F.a == "hw_for"))
async def hw_for(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    s = await sv.student(callback_data.i)
    await collect.begin(state, HwAssign.collect, sid=s["id"])
    await cq.message.answer(
        f"📚 ДЗ для <b>{h(s['name'])}</b>\n\n"
        "Отправь задание: текст, фото, PDF. Можно несколькими сообщениями и альбомом. "
        "Первая строка текста станет названием. Когда всё, жми «✅ Готово».",
        reply_markup=kb.COLLECT_KB,
    )
    await cq.answer()


@router.message(HwAssign.collect, F.text == kb.DONE)
async def hw_done(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("items"):
        await message.answer("Сначала пришли хотя бы что-то: текст, фото или файл.")
        return
    await state.set_state(HwAssign.deadline)
    await message.answer(f"Материалов: {len(data['items'])}.", reply_markup=kb.ADMIN_MENU)
    await message.answer(
        "Срок сдачи? Выбери или напиши дату, например: 15.11 или 15.11 20:00",
        reply_markup=await deadline_kb(data["sid"]),
    )


@router.callback_query(HwAssign.collect, CB.filter(F.a == "col_done"))
async def hw_done_btn(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await drop_kb(cq.message)
    await hw_done(cq.message, state)


@router.message(HwAssign.collect)
async def hw_collect(message: Message, state: FSMContext):
    await collect.add(message, state)


@router.callback_query(HwAssign.deadline, CB.filter(F.a == "dl"))
async def hw_deadline_btn(cq: CallbackQuery, callback_data: CB, state: FSMContext, bot: Bot):
    data = await state.get_data()
    v = callback_data.v
    if v == "next":
        nl = await sv.next_lesson(data["sid"])
        dl = tu.parse_iso(nl["start_utc"]) if nl else None
    elif v == "none":
        dl = None
    else:
        day = tu.now_utc().astimezone(cfg.tz).date() + timedelta(days=int(v))
        dl = datetime.combine(day, time(23, 59), tzinfo=cfg.tz).astimezone(tu.UTC)
    await cq.answer()
    await drop_kb(cq.message)
    await finalize(bot, cq.message, state, dl)


@router.message(HwAssign.deadline, F.text)
async def hw_deadline_text(message: Message, state: FSMContext, bot: Bot):
    dl = tu.parse_dt(message.text, cfg.tz, need_time=False)
    if not dl:
        await message.answer("Не понял дату. Пример: 15.11 или 15.11 20:00")
        return
    if dl <= tu.now_utc():
        await message.answer("Эта дата уже прошла.")
        return
    await finalize(bot, message, state, dl)


async def finalize(bot: Bot, msg: Message, state: FSMContext, dl: datetime | None) -> None:
    data = await state.get_data()
    await state.clear()
    sid, items = data["sid"], data["items"]
    now = tu.now_utc()
    title = make_title(items, f"ДЗ от {now.astimezone(cfg.tz):%d.%m}")
    hid = await db.run(
        "INSERT INTO homework(student_id, title, deadline_utc, created_at) VALUES(?,?,?,?)",
        sid, title, tu.iso(dl) if dl else None, tu.iso(now),
    )
    await db.run_many(
        "INSERT INTO hw_items(hw_id, kind, round, type, file_id, text, created_at) VALUES(?,?,?,?,?,?,?)",
        [(hid, "task", 0, it["type"], it.get("file_id"), it.get("text"), tu.iso(now)) for it in items],
    )
    delivered = await sv.deliver_hw(bot, hid)
    s = await sv.student(sid)
    stz = tu.zone(s["tz"])
    await to_members(
        bot, sid, "parent",
        f"📚 Новое домашнее задание для <b>{h(s['name'])}</b>: «{h(title)}».\n"
        f"Срок: {tu.fmt_dt(dl, stz) if dl else 'без срока'}",
    )
    text = (
        f"✅ ДЗ «{h(title)}» выдано: <b>{h(s['name'])}</b>\n"
        f"Срок: {tu.fmt_dt(dl, cfg.tz) if dl else 'без срока'}"
    )
    if not delivered:
        text += "\n\n⚠️ Ученик ещё не подключён к боту. ДЗ сохранено, он увидит его в «📚 Домашка» после подключения."
    await msg.answer(text, reply_markup=kb.ADMIN_MENU)


@router.callback_query(CB.filter(F.a == "rv"))
async def rv_open(cq: CallbackQuery, callback_data: CB, bot: Bot):
    await cq.answer()
    row = await sv.hw(callback_data.i)
    if not row or row["status"] != "submitted":
        await cq.message.answer("Это ДЗ уже проверено.")
        return
    await sv.send_review(bot, cq.message.chat.id, callback_data.i)


@router.callback_query(CB.filter(F.a == "hwtask"))
async def hw_task(cq: CallbackQuery, callback_data: CB, bot: Bot):
    await cq.answer()
    await sv.show_task(bot, cq.message.chat.id, callback_data.i)


@router.callback_query(CB.filter(F.a == "hwsol"))
async def hw_solution(cq: CallbackQuery, callback_data: CB, bot: Bot):
    await cq.answer()
    row = await sv.hw(callback_data.i)
    if not row["round"]:
        await cq.message.answer("Решение ещё не сдавали.")
        return
    if row["status"] == "submitted":
        await sv.send_review(bot, cq.message.chat.id, row["id"])
        return
    await send(bot, cq.message.chat.id, f"📥 Последнее решение «{h(row['title'])}» (попытка {row['round']})")
    await send_items(bot, cq.message.chat.id, await sv.hw_items(row["id"], "solution", row["round"]))


@router.callback_query(CB.filter(F.a.in_({"hwacc", "hwredo"})))
async def hw_verdict(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    row = await sv.hw(callback_data.i)
    if not row or row["status"] != "submitted":
        await cq.answer("Это ДЗ уже проверено", show_alert=True)
        return
    ok = callback_data.a == "hwacc"
    await state.set_state(Review.comment)
    await state.set_data({"hid": row["id"], "ok": ok})
    verdict = "✅ Принимаем" if ok else "🔁 На доработку"
    await cq.message.answer(
        f"{verdict}: «{h(row['title'])}» ({h(row['name'])}).\nНапиши комментарий для ученика или нажми кнопку.",
        reply_markup=ikb([btn("Без комментария", "rv_nc")]),
    )
    await cq.answer()


@router.callback_query(Review.comment, CB.filter(F.a == "rv_nc"))
async def review_no_comment(cq: CallbackQuery, state: FSMContext, bot: Bot):
    await cq.answer()
    await drop_kb(cq.message)
    await apply_review(bot, cq.message, state, None)


@router.message(Review.comment, F.text)
async def review_comment(message: Message, state: FSMContext, bot: Bot):
    await apply_review(bot, message, state, message.html_text)


async def apply_review(bot: Bot, msg: Message, state: FSMContext, comment: str | None) -> None:
    data = await state.get_data()
    await state.clear()
    row = await sv.hw(data["hid"])
    if not row or row["status"] != "submitted":
        await msg.answer("Это ДЗ уже проверено.")
        return
    ok = data["ok"]
    now = tu.now_utc()
    dl = row["deadline_utc"]
    if not ok and dl and tu.parse_iso(dl) < now:
        dl = None
    await db.run(
        "UPDATE homework SET status=?, comment=?, checked_at=?, deadline_utc=?, remind_soon=0, remind_over=0 WHERE id=?",
        "accepted" if ok else "redo", comment, tu.iso(now), dl, row["id"],
    )
    c = f"\n\n💬 {comment}" if comment else ""
    sid = row["student_id"]
    if ok:
        await to_members(bot, sid, "student", f"✅ ДЗ «{h(row['title'])}» принято!{c}")
        await to_members(
            bot, sid, "parent",
            f"✅ <b>{h(row['name'])}</b>: домашнее задание «{h(row['title'])}» сдано и принято."
            + (f"\n\nКомментарий репетитора: {comment}" if comment else ""),
        )
    else:
        await to_members(
            bot, sid, "student", f"🔁 ДЗ «{h(row['title'])}» нужно доработать.{c}",
            reply_markup=ikb([btn("📤 Сдать снова", "hws", row["id"])]),
        )
    await msg.answer(f"Готово: «{h(row['title'])}» {'принято ✅' if ok else 'отправлено на доработку 🔁'}")


@router.callback_query(CB.filter(F.a == "hw_hist"))
async def hw_history(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await state.clear()
    sid = callback_data.i
    s = await sv.student(sid)
    rows = await db.rows(sv.HW_SQL + " WHERE hw.student_id=? ORDER BY hw.id DESC LIMIT 15", sid)
    text = f"📖 Домашки: <b>{h(s['name'])}</b>"
    if rows:
        text += "\n\n📝 задано, 📥 на проверке, 🔁 доработка, ✅ принято, ⚠️ просрочено"
    else:
        text += "\n\nПока ни одной."
    buttons = [[btn(f"{sv.HW_ICON[sv.hw_state(r)]} {r['title']}"[:60], "hwa", r["id"])] for r in rows]
    buttons.append([btn("📚 Выдать новое", "hw_for", sid), btn("« Назад", "st", sid)])
    await edit(cq.message, text, ikb(*buttons))
    await cq.answer()


@router.callback_query(CB.filter(F.a == "hwa"))
async def hw_card(cq: CallbackQuery, callback_data: CB):
    row = await sv.hw(callback_data.i)
    if not row:
        await cq.answer("Не найдено", show_alert=True)
        return
    state_ = sv.hw_state(row)
    lines = [
        f"{sv.HW_ICON[state_]} <b>{h(row['title'])}</b>",
        f"Ученик: {h(row['name'])}",
        f"Выдано: {tu.fmt_day(tu.parse_iso(row['created_at']), cfg.tz)}",
        f"Срок: {sv.deadline_text(row, cfg.tz)}",
        f"Статус: {sv.HW_RU[state_]}",
    ]
    if row["submitted_at"]:
        lines.append(f"Сдано: {tu.fmt_dt(tu.parse_iso(row['submitted_at']), cfg.tz)} (попыток: {row['round']})")
    if row["comment"]:
        lines.append(f"Комментарий: {row['comment']}")
    hid = row["id"]
    rows = [[btn("📎 Задание", "hwtask", hid)] + ([btn("📥 Решение", "hwsol", hid)] if row["round"] else [])]
    if row["status"] == "submitted":
        rows.append([btn("✅ Принять", "hwacc", hid), btn("🔁 На доработку", "hwredo", hid)])
    rows.append([btn("🗑 Удалить", "hwdelq", hid), btn("« Назад", "hw_hist", row["student_id"])])
    await edit(cq.message, "\n".join(lines), ikb(*rows))
    await cq.answer()


@router.callback_query(CB.filter(F.a == "hwdelq"))
async def hw_delete_q(cq: CallbackQuery, callback_data: CB):
    row = await sv.hw(callback_data.i)
    await edit(
        cq.message,
        f"Удалить ДЗ «{h(row['title'])}» вместе с решениями?",
        ikb([btn("Да, удалить", "hwdel", row["id"]), btn("Нет", "hwa", row["id"])]),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "hwdel"))
async def hw_delete(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    row = await sv.hw(callback_data.i)
    if row:
        await db.run("DELETE FROM hw_items WHERE hw_id=?", row["id"])
        await db.run("DELETE FROM homework WHERE id=?", row["id"])
        callback_data = CB(a="hw_hist", i=row["student_id"])
        await hw_history(cq, callback_data, state)
    else:
        await cq.answer("Уже удалено")
