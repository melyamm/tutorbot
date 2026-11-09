from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import db, services as sv, timeutil as tu
from ..config import cfg
from ..kb import CB, btn, ikb
from ..notify import drop_kb, edit, send, to_members
from ..states import PayFix
from ..util import h, parse_int, rub

router = Router(name="admin_money")


async def finance_since():
    raw = await db.kv_get("finance_since")
    return tu.parse_iso(raw) if raw else None


async def _month_stats(a, b) -> tuple[int, int, int]:
    since = await finance_since()
    if since and since > a:
        a = since
    if a >= b:
        return 0, 0, 0
    lessons = await db.one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS s FROM ledger "
        "WHERE kind='lesson' AND created_at>=? AND created_at<?",
        tu.iso(a), tu.iso(b),
    )
    paid = await db.val(
        "SELECT COALESCE(SUM(-amount), 0) FROM ledger WHERE kind='payment' AND created_at>=? AND created_at<?",
        tu.iso(a), tu.iso(b), default=0,
    )
    return lessons["n"], lessons["s"], paid


async def debtors() -> list[tuple]:
    bals = await sv.balances()
    out = []
    for sid, b in bals.items():
        if b > 0:
            s = await sv.student(sid)
            out.append((s, b))
    out.sort(key=lambda x: -x[1])
    return out


async def finance_view():
    now_l = tu.now_utc().astimezone(cfg.tz)
    m0 = now_l.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    m_next = (m0 + timedelta(days=32)).replace(day=1)
    m_prev = (m0 - timedelta(days=1)).replace(day=1)
    lines = ["💰 <b>Финансы</b>", ""]
    since = await finance_since()
    if since:
        lines.append(f"📍 Статистика считается с {since.astimezone(cfg.tz):%d.%m.%Y %H:%M}")
        lines.append("")
    for a, b in ((m0, m_next), (m_prev, m0)):
        n, s, p = await _month_stats(a, b)
        lines.append(f"<b>{tu.MONTHS_NOM[a.month - 1]}</b>: занятий {n} на {rub(s)}, получено {rub(p)}")
    ds = await debtors()
    lines.append("")
    if ds:
        lines.append(f"<b>Должны</b> (всего {rub(sum(b for _, b in ds))}):")
        for s, b in ds:
            lines.append(f"• {h(s['name'])}: {rub(b)}" + ("" if s["active"] else " (архив)"))
    else:
        lines.append("Долгов нет ✅")
    pending = await db.val("SELECT COUNT(*) FROM pay_requests WHERE status='pending'", default=0)
    rows = []
    if pending:
        rows.append([btn(f"🧾 Заявки на оплату ({pending})", "pay_list")])
    if ds:
        rows.append([btn("🔔 Напомнить должникам", "money_rq")])
    rows.append([btn("⚙️ Корректировка", "fin_cfg"), btn("🔄 Обновить", "money")])
    return "\n".join(lines), ikb(*rows)


@router.callback_query(CB.filter(F.a == "fin_cfg"))
async def cb_fin_cfg(cq: CallbackQuery):
    since = await finance_since()
    ds = await debtors()
    rows = [[btn("📍 Считать статистику с этой минуты", "fin_since")]]
    if since:
        rows.append([btn("↩️ Считать за всё время", "fin_all")])
    if ds:
        rows.append([btn(f"🧽 Списать все долги ({rub(sum(b for _, b in ds))})", "fin_wo_q")])
    rows.append([btn("« Финансы", "money")])
    await edit(
        cq.message,
        "⚙️ <b>Корректировка финансов</b>\n\n"
        "📍 <b>Считать с этой минуты</b>: доход и полученные оплаты за месяц будут считаться только с текущего "
        "момента, старые занятия и оплаты в сводку не попадут. История операций не удаляется.\n\n"
        "🧽 <b>Списать долги</b>: обнуляет долги всех учеников. Это не считается полученной оплатой.\n\n"
        "Поправить отдельного ученика: карточка ученика → «📜 Операции».",
        ikb(*rows),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "fin_since"))
async def cb_fin_since(cq: CallbackQuery):
    await db.kv_set("finance_since", tu.iso(tu.now_utc()))
    text, markup = await finance_view()
    await edit(cq.message, "✅ Статистика теперь считается с этой минуты.\n\n" + text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "fin_all"))
async def cb_fin_all(cq: CallbackQuery):
    await db.run("DELETE FROM kv WHERE k='finance_since'")
    text, markup = await finance_view()
    await edit(cq.message, "✅ Статистика снова за всё время.\n\n" + text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "fin_wo_q"))
async def cb_fin_wo_q(cq: CallbackQuery):
    ds = await debtors()
    await edit(
        cq.message,
        f"Списать долги у {len(ds)} учеников на {rub(sum(b for _, b in ds))}? Отменить это можно будет "
        "только удалением операций вручную у каждого ученика.",
        ikb([btn("Да, списать", "fin_wo"), btn("Нет", "fin_cfg")]),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "fin_wo"))
async def cb_fin_wo(cq: CallbackQuery):
    now = tu.iso(tu.now_utc())
    ds = await debtors()
    await db.run_many(
        "INSERT INTO ledger(student_id, amount, kind, note, created_at) VALUES(?,?,?,?,?)",
        [(s["id"], -b, "writeoff", "долг списан", now) for s, b in ds],
    )
    text, markup = await finance_view()
    await edit(cq.message, f"🧽 Списано {rub(sum(b for _, b in ds))}.\n\n" + text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "money"))
async def cb_money(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = await finance_view()
    await edit(cq.message, text, markup)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "money_rq"))
async def cb_remind_q(cq: CallbackQuery):
    ds = await debtors()
    await edit(
        cq.message,
        f"Отправить напоминание об оплате родителям ({len(ds)} учеников)?",
        ikb([btn("Да, отправить", "money_rs"), btn("Нет", "money")]),
    )
    await cq.answer()


@router.callback_query(CB.filter(F.a == "money_rs"))
async def cb_remind(cq: CallbackQuery, bot: Bot):
    sent, missing = 0, []
    for s, b in await debtors():
        if not s["active"]:
            continue
        text = f"Здравствуйте! Напоминаю об оплате занятий <b>{h(s['name'])}</b>: {rub(b)}."
        if cfg.pay_info:
            text += f"\n\n{h(cfg.pay_info)}"
        n = await to_members(
            bot, s["id"], "parent", text, reply_markup=ikb([btn("✅ Я оплатил(а)", "pay_start", s["id"])])
        )
        if n:
            sent += 1
        else:
            missing.append(s["name"])
    text = f"🔔 Напоминание отправлено: {sent}."
    if missing:
        text += "\nРодители не подключены: " + h(", ".join(missing))
    await edit(cq.message, text, ikb([btn("« Финансы", "money")]))
    await cq.answer()


@router.callback_query(CB.filter(F.a == "pay_list"))
async def cb_pay_list(cq: CallbackQuery):
    rows = await db.rows(
        "SELECT p.*, s.name FROM pay_requests p JOIN students s ON s.id=p.student_id "
        "WHERE p.status='pending' ORDER BY p.id"
    )
    buttons = [[btn(f"{r['name']}: {rub(r['amount'])}", "pay_view", r["id"])] for r in rows]
    buttons.append([btn("« Финансы", "money")])
    await edit(cq.message, "🧾 <b>Заявки на оплату</b>" + ("" if rows else "\n\nНет."), ikb(*buttons))
    await cq.answer()


@router.callback_query(CB.filter(F.a == "pay_view"))
async def cb_pay_view(cq: CallbackQuery, callback_data: CB, bot: Bot):
    await cq.answer()
    await sv.send_pay_request(bot, cq.message.chat.id, callback_data.i)


async def confirm_payment(bot: Bot, pid: int, amount: int) -> str:
    pr = await db.one("SELECT * FROM pay_requests WHERE id=?", pid)
    if not pr or pr["status"] != "pending":
        return "Эта заявка уже обработана."
    s = await sv.student(pr["student_id"])
    await db.run("UPDATE pay_requests SET status='confirmed', amount=? WHERE id=?", amount, pid)
    await db.run(
        "INSERT INTO ledger(student_id, amount, kind, note, created_at) VALUES(?,?,?,?,?)",
        s["id"], -amount, "payment", f"оплата (заявка #{pid})", tu.iso(tu.now_utc()),
    )
    bal = await sv.balance(s["id"])
    await send(
        bot, pr["tg_id"],
        f"✅ Оплата {rub(amount)} за занятия <b>{h(s['name'])}</b> подтверждена. Спасибо!\n{sv.balance_line_parent(bal)}",
    )
    return f"✅ Оплата {rub(amount)} от {h(s['name'])} подтверждена. {sv.balance_line(bal)}"


@router.callback_query(CB.filter(F.a == "pay_ok"))
async def cb_pay_ok(cq: CallbackQuery, callback_data: CB, bot: Bot):
    pr = await db.one("SELECT * FROM pay_requests WHERE id=?", callback_data.i)
    result = await confirm_payment(bot, callback_data.i, pr["amount"] if pr else 0)
    await drop_kb(cq.message)
    await cq.message.answer(result)
    await cq.answer()


@router.callback_query(CB.filter(F.a == "pay_fix"))
async def cb_pay_fix(cq: CallbackQuery, callback_data: CB, state: FSMContext):
    pr = await db.one("SELECT * FROM pay_requests WHERE id=?", callback_data.i)
    if not pr or pr["status"] != "pending":
        await cq.answer("Уже обработано", show_alert=True)
        return
    await state.set_state(PayFix.amount)
    await state.set_data({"pid": pr["id"]})
    await cq.message.answer("Какая сумма пришла на самом деле? В рублях.")
    await cq.answer()


@router.message(PayFix.amount, F.text)
async def pay_fix_amount(message: Message, state: FSMContext, bot: Bot):
    amount = parse_int(message.text)
    if not amount:
        await message.answer("Нужно число больше нуля, например 3000")
        return
    data = await state.get_data()
    await state.clear()
    await message.answer(await confirm_payment(bot, data["pid"], amount))


@router.callback_query(CB.filter(F.a == "pay_no"))
async def cb_pay_no(cq: CallbackQuery, callback_data: CB, bot: Bot):
    pr = await db.one("SELECT * FROM pay_requests WHERE id=?", callback_data.i)
    if not pr or pr["status"] != "pending":
        await cq.answer("Уже обработано", show_alert=True)
        return
    await db.run("UPDATE pay_requests SET status='rejected' WHERE id=?", pr["id"])
    await send(
        bot, pr["tg_id"],
        f"❌ Репетитор не нашёл перевод на {rub(pr['amount'])}. Свяжитесь с ним, пожалуйста.",
    )
    await drop_kb(cq.message)
    await cq.message.answer("❌ Заявка отклонена, родитель уведомлён.")
    await cq.answer()
