from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import db, kb, services as sv, timeutil as tu
from ..config import cfg
from ..kb import CB, btn, ikb
from ..notify import edit, to_members
from ..states import AddStudent, AdminPay, EditField, OneOff, SlotAdd
from ..util import h, normalize_grade, parse_int, rub

router = Router(name="admin_students")

FIELDS = {"name": "Имя", "grade": "Класс", "subject": "Предмет и цель", "rate": "Ставка за 60 мин"}


async def students_list():
    rows = await sv.active_students()
    bals = await sv.balances()
    buttons = []
    for s in rows:
        b = bals.get(s["id"], 0)
        buttons.append([btn(s["name"] + (f" · долг {rub(b)}" if b > 0 else ""), "st", s["id"])])
    buttons.append([btn("➕ Новый ученик", "st_new"), btn("🗄 Архив", "st_arch")])
    text = f"👥 <b>Ученики</b>: {len(rows)}" if rows else "Пока нет ни одного ученика. Добавь первого 👇"
    return text, ikb(*buttons)


def card_kb(s):
    sid = s["id"]
    if not s["active"]:
        return ikb([btn("♻️ Вернуть из архива", "st_unarch", sid)], [btn("« К списку", "st_list")])
    return ikb(
        [btn("🔗 Ссылка ученику", "inv", sid, "student"), btn("🔗 Ссылка родителю", "inv", sid, "parent")],
        [btn("🕒 Расписание", "slots", sid), btn("➕ Разовое занятие", "oo", sid)],
        [btn("📚 Выдать ДЗ", "hw_for", sid), btn("📖 Домашки", "hw_hist", sid)],
        [btn("💰 Внести оплату", "pay_add", sid, "-"), btn("📜 Операции", "ops", sid)],
        [btn("✏️ Изменить", "st_edit", sid), btn("🗄 В архив", "st_archq", sid)],
        [btn("« К списку", "st_list")],
    )


async def show_card(msg: Message, sid: int, as_edit: bool = False) -> None:
    s = await sv.student(sid)
    text = await sv.card_text(sid)
    if as_edit:
        await edit(msg, text, card_kb(s))
    else:
        await msg.answer(text, reply_markup=card_kb(s))


async def start_new(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddStudent.name)
    await msg.answer("Как зовут ученика? Напиши имя (и фамилию, если нужно).")


@router.callback_query(CB.filter(F.a == "st_list"))
async def cb_list(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = await students_list()
    await edit(cq.message, text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "st"))
async def cb_card(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await state.clear()
    if not await sv.student(callback_data.i):
        await cq.answer("Ученик не найден", show_alert=True)
        return
    await show_card(cq.message, callback_data.i, as_edit=True)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "st_new"))
async def cb_new(cq: CallbackQuery, state: FSMContext):
    await start_new(cq.message, state)
    await cq.answer()


@router.message(AddStudent.name, F.text)
async def add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip()[:60])
    await state.set_state(AddStudent.grade)
    await message.answer("Класс? Например: 9", reply_markup=ikb([btn("Пропустить", "skip")]))


async def _after_grade(msg: Message, state: FSMContext, grade: str | None) -> None:
    await state.update_data(grade=grade)
    await state.set_state(AddStudent.subject)
    await msg.answer(
        "Предмет и цель? Например: «Математика, ОГЭ» или «Алгебра, успеваемость».",
        reply_markup=ikb([btn("Пропустить", "skip")]),
    )


@router.message(AddStudent.grade, F.text)
async def add_grade(message: Message, state: FSMContext):
    await _after_grade(message, state, normalize_grade(message.text))


@router.callback_query(AddStudent.grade, CB.filter(F.a == "skip"))
async def skip_grade(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await _after_grade(cq.message, state, None)


async def _after_subject(msg: Message, state: FSMContext, subject: str | None) -> None:
    await state.update_data(subject=subject)
    await state.set_state(AddStudent.rate)
    await msg.answer("Ставка за 60 минут, в рублях? Например: 1500")


@router.message(AddStudent.subject, F.text)
async def add_subject(message: Message, state: FSMContext):
    await _after_subject(message, state, message.text.strip()[:60])


@router.callback_query(AddStudent.subject, CB.filter(F.a == "skip"))
async def skip_subject(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await _after_subject(cq.message, state, None)


@router.message(AddStudent.rate, F.text)
async def add_rate(message: Message, state: FSMContext):
    rate = parse_int(message.text)
    if rate is None:
        await message.answer("Нужно число, например 1500")
        return
    data = await state.get_data()
    await state.clear()
    sid = await db.run(
        "INSERT INTO students(name, grade, subject, rate, tz, created_at) VALUES(?,?,?,?,?,?)",
        data["name"], data.get("grade"), data.get("subject"), rate, cfg.tz.key, tu.iso(tu.now_utc()),
    )
    await message.answer("✅ Ученик добавлен. Дальше: задай расписание и отправь ссылки ученику и родителю.")
    await show_card(message, sid)


@router.callback_query(CB.filter(F.a == "inv"))
async def cb_invite(cq: CallbackQuery, callback_data: CB):
    sid, role = callback_data.i, callback_data.v
    s = await sv.student(sid)
    code = await sv.create_invite(sid, role)
    link = f"https://t.me/{sv.BOT_USERNAME}?start={code}"
    if role == "student":
        await cq.message.answer(
            f"🔗 Ссылка для ученика <b>{h(s['name'])}</b> (одноразовая). Перешли сообщение ниже ученику 👇"
        )
        await cq.message.answer(
            f"Привет! Подключись к моему боту: там напоминания о занятиях и домашка, решения тоже сдаёшь туда.\n\n{link}",
            disable_web_page_preview=True,
        )
    else:
        await cq.message.answer(
            f"🔗 Ссылка для родителя <b>{h(s['name'])}</b> (одноразовая, для второго родителя создай ещё одну). "
            "Перешли сообщение ниже 👇"
        )
        await cq.message.answer(
            "Здравствуйте! Подключитесь, пожалуйста, к моему боту: туда будут приходить напоминания о занятиях, "
            f"статус домашних заданий и информация об оплате.\n\n{link}",
            disable_web_page_preview=True,
        )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "st_archq"))
async def cb_archive_q(cq: CallbackQuery, callback_data: CB):
    sid = callback_data.i
    s = await sv.student(sid)
    await edit(
        cq.message,
        f"Отправить <b>{h(s['name'])}</b> в архив?\n\nРасписание удалится, будущие занятия отменятся. "
        "Долг и история сохранятся, ученик и родители перестанут получать уведомления.",
        ikb([btn("Да, в архив", "st_arch_yes", sid), btn("Отмена", "st", sid)]),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "st_arch_yes"))
async def cb_archive(cq: CallbackQuery, callback_data: CB):
    sid = callback_data.i
    await db.run("UPDATE students SET active=0 WHERE id=?", sid)
    await db.run("UPDATE slots SET active=0 WHERE student_id=?", sid)
    await db.run(
        "DELETE FROM lessons WHERE student_id=? AND status='planned' AND start_utc>?", sid, tu.iso(tu.now_utc())
    )
    text, markup = await students_list()
    await edit(cq.message, "🗄 Ученик в архиве.\n\n" + text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "st_arch"))
async def cb_archived(cq: CallbackQuery):
    rows = await db.rows("SELECT * FROM students WHERE active=0 ORDER BY name")
    buttons = [[btn(s["name"], "st", s["id"])] for s in rows]
    buttons.append([btn("« К списку", "st_list")])
    await edit(cq.message, "🗄 <b>Архив</b>" + ("" if rows else "\n\nПусто."), ikb(*buttons))
    await cq.answer()


@router.callback_query(CB.filter(F.a == "st_unarch"))
async def cb_unarchive(cq: CallbackQuery, callback_data: CB):
    await db.run("UPDATE students SET active=1 WHERE id=?", callback_data.i)
    await show_card(cq.message, callback_data.i, as_edit=True)
    await cq.answer("Ученик возвращён. Расписание нужно задать заново.", show_alert=True)


@router.callback_query(CB.filter(F.a == "st_edit"))
async def cb_edit(cq: CallbackQuery, callback_data: CB):
    sid = callback_data.i
    await edit(
        cq.message,
        "Что изменить?",
        ikb(
            [btn("Имя", "st_f", sid, "name"), btn("Класс", "st_f", sid, "grade")],
            [btn("Предмет", "st_f", sid, "subject"), btn("Ставка", "st_f", sid, "rate")],
            [btn("🌍 Часовой пояс", "st_tz", sid)],
            [btn("« Назад", "st", sid)],
        ),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "st_f"))
async def cb_field(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    if callback_data.v not in FIELDS:
        await cq.answer()
        return
    await state.set_state(EditField.value)
    await state.set_data({"sid": callback_data.i, "field": callback_data.v})
    hint = "\nСтавка меняется только для будущих занятий." if callback_data.v == "rate" else ""
    await cq.message.answer(f"Новое значение: {FIELDS[callback_data.v]}{hint}")
    await cq.answer()


@router.message(EditField.value, F.text)
async def field_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data["field"]
    if field == "rate":
        value = parse_int(message.text)
        if value is None:
            await message.answer("Нужно число, например 1500")
            return
    elif field == "grade":
        value = normalize_grade(message.text)
    else:
        value = message.text.strip()[:60]
    await state.clear()
    await db.run(f"UPDATE students SET {field}=? WHERE id=?", value, data["sid"])
    await show_card(message, data["sid"])


@router.callback_query(CB.filter(F.a == "st_tz"))
async def cb_tz(cq: CallbackQuery, callback_data: CB):
    sid = callback_data.i
    rows, row = [], []
    for idx, (city, _) in enumerate(tu.TZ_CHOICES):
        row.append(btn(city, "st_tzset", sid, str(idx)))
        if len(row) == 2:
            rows.append(row)
            row = []
    rows.append(row)
    rows.append([btn("« Назад", "st", sid)])
    await edit(
        cq.message,
        "Часовой пояс ученика? Ученик и родители будут видеть время занятий в своём поясе, "
        "а ты в своём.",
        ikb(*rows),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "st_tzset"))
async def cb_tz_set(cq: CallbackQuery, callback_data: CB):
    _, tz = tu.TZ_CHOICES[int(callback_data.v)]
    await db.run("UPDATE students SET tz=? WHERE id=?", tz, callback_data.i)
    await show_card(cq.message, callback_data.i, as_edit=True)
    await cq.answer("Сохранено")


async def slots_view(sid: int):
    s = await sv.student(sid)
    slots = await db.rows("SELECT * FROM slots WHERE student_id=? AND active=1 ORDER BY weekday, hhmm", sid)
    lines = [f"🕒 Регулярные занятия: <b>{h(s['name'])}</b>", ""]
    if slots:
        lines += [f"• {tu.WD_FULL[x['weekday']].capitalize()}, {x['hhmm']}, {x['duration']} мин" for x in slots]
    else:
        lines.append("Пока нет.")
    lines.append(f"\nВремя: {tu.tz_label(cfg.tz.key)}")
    buttons = [[btn(f"❌ Убрать {tu.WD[x['weekday']]} {x['hhmm']}", "slot_del", x["id"])] for x in slots]
    buttons.append([btn("➕ Добавить", "slot_add", sid), btn("« Назад", "st", sid)])
    return "\n".join(lines), ikb(*buttons)


@router.callback_query(CB.filter(F.a == "slots"))
async def cb_slots(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await state.clear()
    text, markup = await slots_view(callback_data.i)
    await edit(cq.message, text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "slot_add"))
async def cb_slot_add(cq: CallbackQuery, callback_data: CB):
    sid = callback_data.i
    days = [btn(tu.WD[i], "slot_wd", sid, str(i)) for i in range(7)]
    await edit(cq.message, "В какой день недели?", ikb(days[:4], days[4:], [btn("« Назад", "slots", sid)]))
    await cq.answer()


@router.callback_query(CB.filter(F.a == "slot_wd"))
async def cb_slot_wd(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await state.set_state(SlotAdd.time)
    await state.set_data({"sid": callback_data.i, "wd": int(callback_data.v)})
    await cq.message.answer(f"{tu.WD_FULL[int(callback_data.v)].capitalize()}. Во сколько? Например: 18:00")
    await cq.answer()


@router.message(SlotAdd.time, F.text)
async def slot_time(message: Message, state: FSMContext):
    hm = tu.parse_hhmm(message.text)
    if not hm:
        await message.answer("Не понял время. Пример: 18:00")
        return
    await state.update_data(hhmm=hm)
    await state.set_state(SlotAdd.duration)
    data = await state.get_data()
    await message.answer("Сколько длится?", reply_markup=kb.dur_kb("slot_dur", data["sid"]))


@router.callback_query(SlotAdd.duration, CB.filter(F.a == "slot_dur"))
async def slot_duration(cq: CallbackQuery, callback_data: CB, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    sid, dur = data["sid"], int(callback_data.v)
    slot_id = await db.run(
        "INSERT INTO slots(student_id, weekday, hhmm, duration, created_at) VALUES(?,?,?,?,?)",
        sid, data["wd"], data["hhmm"], dur, tu.iso(tu.now_utc()),
    )
    await sv.ensure_lessons()
    s = await sv.student(sid)
    first = await db.one(
        "SELECT * FROM lessons WHERE slot_id=? AND status='planned' ORDER BY start_utc LIMIT 1", slot_id
    )
    if first:
        start = tu.parse_iso(first["start_utc"])
        stz = tu.zone(s["tz"])
        local = start.astimezone(stz)
        info = f"{tu.WD_PLURAL[local.weekday()]} в {local:%H:%M}, {dur} мин. Первое: {tu.fmt_dt(start, stz)}."
        await to_members(bot, sid, "student", f"📅 Новое регулярное занятие: {info}")
        await to_members(bot, sid, "parent", f"📅 <b>{h(s['name'])}</b>: новое регулярное занятие {info}")
    await cq.answer("Добавлено")
    text, markup = await slots_view(sid)
    await edit(cq.message, "✅ Добавлено.\n\n" + text, markup)


@router.callback_query(CB.filter(F.a == "slot_del"))
async def cb_slot_del(cq: CallbackQuery, callback_data: CB, bot: Bot):
    sl = await db.one("SELECT * FROM slots WHERE id=?", callback_data.i)
    if not sl or not sl["active"]:
        await cq.answer("Уже удалено")
        return
    await db.run("UPDATE slots SET active=0 WHERE id=?", sl["id"])
    await db.run(
        "DELETE FROM lessons WHERE slot_id=? AND status='planned' AND start_utc>?", sl["id"], tu.iso(tu.now_utc())
    )
    s = await sv.student(sl["student_id"])
    wd, hm = sv.slot_local(sl, tu.zone(s["tz"]))
    text = f"регулярные занятия {tu.WD_PLURAL[wd]} в {hm} убраны из расписания."
    await to_members(bot, s["id"], "student", "📅 " + text.capitalize())
    await to_members(bot, s["id"], "parent", f"📅 <b>{h(s['name'])}</b>: {text}")
    text, markup = await slots_view(sl["student_id"])
    await edit(cq.message, text, markup)
    await cq.answer("Убрано")


@router.callback_query(CB.filter(F.a == "oo"))
async def cb_oneoff(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await state.set_state(OneOff.when)
    await state.set_data({"sid": callback_data.i})
    await cq.message.answer(
        f"Когда занятие? Дата и время ({tu.tz_label(cfg.tz.key)}), например: 15.11 18:00 или завтра 17:30"
    )
    await cq.answer()


@router.message(OneOff.when, F.text)
async def oneoff_when(message: Message, state: FSMContext):
    when = tu.parse_dt(message.text, cfg.tz)
    if not when:
        await message.answer("Не понял. Пример: 15.11 18:00")
        return
    if when <= tu.now_utc():
        await message.answer("Это время уже прошло.")
        return
    await state.update_data(when=tu.iso(when))
    await state.set_state(OneOff.duration)
    data = await state.get_data()
    await message.answer(f"{tu.fmt_dt(when, cfg.tz)}. Сколько длится?", reply_markup=kb.dur_kb("oo_dur", data["sid"]))


@router.callback_query(OneOff.duration, CB.filter(F.a == "oo_dur"))
async def oneoff_duration(cq: CallbackQuery, callback_data: CB, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    sid, dur = data["sid"], int(callback_data.v)
    lid = await db.run(
        "INSERT INTO lessons(student_id, start_utc, duration, created_at) VALUES(?,?,?,?)",
        sid, data["when"], dur, tu.iso(tu.now_utc()),
    )
    s = await sv.student(sid)
    start = tu.parse_iso(data["when"])
    stz = tu.zone(s["tz"])
    await to_members(bot, sid, "student", f"📅 Назначено занятие: {tu.fmt_dt(start, stz)}, {dur} мин.")
    await to_members(bot, sid, "parent", f"📅 <b>{h(s['name'])}</b>: назначено занятие {tu.fmt_dt(start, stz)}, {dur} мин.")
    await edit(
        cq.message,
        f"✅ Занятие добавлено: <b>{h(s['name'])}</b>, {tu.fmt_dt(start, cfg.tz)}, {dur} мин.",
        ikb([btn("Открыть", "ls", lid), btn("👤 Ученик", "st", sid)]),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "ops"))
async def cb_ops(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    await state.clear()
    text, markup = await ops_view(callback_data.i)
    await edit(cq.message, text, markup)
    await cq.answer()


async def ops_view(sid: int):
    s = await sv.student(sid)
    rows = await db.rows("SELECT * FROM ledger WHERE student_id=? ORDER BY id DESC LIMIT 15", sid)
    lines = [f"📜 Операции: <b>{h(s['name'])}</b>", sv.balance_line(await sv.balance(sid)), ""]
    if rows:
        for r in rows:
            d = tu.parse_iso(r["created_at"]).astimezone(cfg.tz)
            sign = "+" if r["amount"] > 0 else "-"
            lines.append(f"{d:%d.%m} · {h(r['note'] or r['kind'])} · {sign}{rub(abs(r['amount']))}")
        lines.append("\n+ начисление, - оплата или списание")
    else:
        lines.append("Операций пока нет.")
    bal = await sv.balance(sid)
    return (
        "\n".join(lines),
        ikb(
            [btn("💰 Внести оплату", "pay_add", sid, "-"), btn("➕ Начислить", "pay_add", sid, "+")],
            [btn(f"🧽 Списать долг {rub(bal)}", "wo_q", sid)] if bal > 0 else [],
            [btn("🗑 Удалить операцию", "op_list", sid)] if rows else [],
            [btn("« Назад", "st", sid)],
        ),
    )


def _op_label(r) -> str:
    d = tu.parse_iso(r["created_at"]).astimezone(cfg.tz)
    sign = "+" if r["amount"] > 0 else "-"
    return f"{d:%d.%m} {r['note'] or r['kind']} {sign}{rub(abs(r['amount']))}"


@router.callback_query(CB.filter(F.a == "op_list"))
async def cb_op_list(cq: CallbackQuery, callback_data: CB):
    sid = callback_data.i
    rows = await db.rows("SELECT * FROM ledger WHERE student_id=? ORDER BY id DESC LIMIT 10", sid)
    buttons = [[btn("🗑 " + _op_label(r)[:55], "op_delq", r["id"])] for r in rows]
    buttons.append([btn("« Назад", "ops", sid)])
    await edit(cq.message, "Какую операцию удалить? Долг пересчитается автоматически.", ikb(*buttons))
    await cq.answer()


@router.callback_query(CB.filter(F.a == "op_delq"))
async def cb_op_delq(cq: CallbackQuery, callback_data: CB):
    r = await db.one("SELECT * FROM ledger WHERE id=?", callback_data.i)
    if not r:
        await cq.answer("Уже удалено")
        return
    await edit(
        cq.message,
        f"Удалить «{h(_op_label(r))}»?",
        ikb([btn("Да, удалить", "op_del", r["id"]), btn("Нет", "op_list", r["student_id"])]),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "op_del"))
async def cb_op_del(cq: CallbackQuery, callback_data: CB):
    r = await db.one("SELECT * FROM ledger WHERE id=?", callback_data.i)
    if r:
        await db.run("DELETE FROM ledger WHERE id=?", r["id"])
        text, markup = await ops_view(r["student_id"])
        await edit(cq.message, "🗑 Удалено.\n\n" + text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "wo_q"))
async def cb_wo_q(cq: CallbackQuery, callback_data: CB):
    sid = callback_data.i
    s = await sv.student(sid)
    bal = await sv.balance(sid)
    await edit(
        cq.message,
        f"Списать долг {rub(bal)} у <b>{h(s['name'])}</b>? Это не считается полученной оплатой.",
        ikb([btn("Да, списать", "wo", sid), btn("Нет", "ops", sid)]),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "wo"))
async def cb_wo(cq: CallbackQuery, callback_data: CB):
    sid = callback_data.i
    bal = await sv.balance(sid)
    if bal > 0:
        await db.run(
            "INSERT INTO ledger(student_id, amount, kind, note, created_at) VALUES(?,?,?,?,?)",
            sid, -bal, "writeoff", "долг списан", tu.iso(tu.now_utc()),
        )
    text, markup = await ops_view(sid)
    await edit(cq.message, (f"🧽 Списано {rub(bal)}.\n\n" if bal > 0 else "") + text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "pay_add"))
async def cb_pay_add(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    sid, sign = callback_data.i, callback_data.v
    s = await sv.student(sid)
    await state.set_state(AdminPay.amount)
    await state.set_data({"sid": sid, "sign": sign})
    if sign == "-":
        debt = await sv.balance(sid)
        markup = ikb([btn(f"{rub(debt)} (весь долг)", "pay_add_q", sid, str(debt))]) if debt > 0 else None
        await cq.message.answer(f"Сколько получил за занятия <b>{h(s['name'])}</b>? В рублях.", reply_markup=markup)
    else:
        await cq.message.answer(
            f"Сколько начислить <b>{h(s['name'])}</b>? Например, долг за занятия до бота. В рублях."
        )
    await cq.answer()


async def _apply_admin_money(bot: Bot, msg: Message, state: FSMContext, amount: int) -> None:
    data = await state.get_data()
    await state.clear()
    sid, sign = data["sid"], data["sign"]
    if sign == "-":
        await db.run(
            "INSERT INTO ledger(student_id, amount, kind, note, created_at) VALUES(?,?,?,?,?)",
            sid, -amount, "payment", "оплата (внесена вручную)", tu.iso(tu.now_utc()),
        )
        bal = await sv.balance(sid)
        await msg.answer(f"✅ Оплата {rub(amount)} записана. {sv.balance_line(bal)}")
    else:
        await db.run(
            "INSERT INTO ledger(student_id, amount, kind, note, created_at) VALUES(?,?,?,?,?)",
            sid, amount, "adjust", "начисление вручную", tu.iso(tu.now_utc()),
        )
        await msg.answer(f"✅ Начислено {rub(amount)}. {sv.balance_line(await sv.balance(sid))}")
    await show_card(msg, sid)


@router.message(AdminPay.amount, F.text)
async def admin_pay_amount(message: Message, state: FSMContext, bot: Bot):
    amount = parse_int(message.text)
    if not amount:
        await message.answer("Нужно число больше нуля, например 3000")
        return
    await _apply_admin_money(bot, message, state, amount)


@router.callback_query(AdminPay.amount, CB.filter(F.a == "pay_add_q"))
async def admin_pay_quick(cq: CallbackQuery, callback_data: CB, state: FSMContext, bot: Bot):
    await cq.answer()
    await _apply_admin_money(bot, cq.message, state, int(callback_data.v))
