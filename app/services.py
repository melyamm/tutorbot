import os
import secrets
import tempfile
from datetime import datetime, time, timedelta

from aiogram import Bot
from aiogram.types import FSInputFile

from . import db, timeutil as tu
from .config import cfg
from .kb import btn, ikb
from .notify import member_ids, send, send_file, send_items
from .util import h, rub

BOT_USERNAME = "bot"

STATUS_RU = {
    "planned": "запланировано",
    "done": "проведено",
    "cancelled": "отменено",
}
STATUS_ICON = {"planned": "🕓", "done": "✅", "cancelled": "❌"}
HW_ICON = {"assigned": "📝", "redo": "🔁", "submitted": "📥", "accepted": "✅", "overdue": "⚠️"}
HW_RU = {
    "assigned": "нужно сделать",
    "redo": "на доработку",
    "submitted": "на проверке",
    "accepted": "принято",
    "overdue": "просрочено",
}

LESSON_SQL = "SELECT l.*, s.name, s.tz, s.rate, s.subject FROM lessons l JOIN students s ON s.id=l.student_id"
HW_SQL = "SELECT hw.*, s.name, s.tz FROM homework hw JOIN students s ON s.id=hw.student_id"


async def student(sid: int):
    return await db.one("SELECT * FROM students WHERE id=?", sid)


async def active_students() -> list:
    return await db.rows("SELECT * FROM students WHERE active=1 ORDER BY name")


async def balances() -> dict[int, int]:
    rs = await db.rows("SELECT student_id, SUM(amount) AS b FROM ledger GROUP BY student_id")
    return {r["student_id"]: r["b"] or 0 for r in rs}


async def balance(sid: int) -> int:
    return await db.val("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE student_id=?", sid, default=0)


def balance_line(b: int) -> str:
    if b > 0:
        return f"Долг: <b>{rub(b)}</b>"
    if b < 0:
        return f"Аванс: {rub(-b)}"
    return "Долгов нет ✅"


def balance_line_parent(b: int) -> str:
    if b > 0:
        return f"К оплате: <b>{rub(b)}</b>"
    if b < 0:
        return f"Аванс: {rub(-b)}"
    return "Задолженности нет ✅"


async def members(sid: int, role: str | None = None) -> list:
    if role:
        return await db.rows("SELECT * FROM members WHERE student_id=? AND role=? ORDER BY id", sid, role)
    return await db.rows("SELECT * FROM members WHERE student_id=? ORDER BY id", sid)


async def member_role(tg_id: int, sid: int) -> str | None:
    rs = await db.rows("SELECT role FROM members WHERE tg_id=? AND student_id=?", tg_id, sid)
    roles = {r["role"] for r in rs}
    if "parent" in roles:
        return "parent"
    if "student" in roles:
        return "student"
    return None


def who(m) -> str:
    if m["username"]:
        return f"@{m['username']}"
    return m["full_name"] or str(m["tg_id"])


async def who_tg(tg_id: int) -> str:
    m = await db.one("SELECT * FROM members WHERE tg_id=? ORDER BY id LIMIT 1", tg_id)
    return who(m) if m else str(tg_id)


async def create_invite(sid: int, role: str) -> str:
    code = secrets.token_urlsafe(9)
    await db.run(
        "INSERT INTO invites(code, student_id, role, created_at) VALUES(?,?,?,?)",
        code, sid, role, tu.iso(tu.now_utc()),
    )
    return code


def lesson_price(rate: int, duration: int, percent: int = 100) -> int:
    return int(round(rate * duration / 60 * percent / 100))


async def lesson(lid: int):
    return await db.one(LESSON_SQL + " WHERE l.id=?", lid)


async def next_lesson(sid: int):
    return await db.one(
        LESSON_SQL + " WHERE l.student_id=? AND l.status='planned' AND l.start_utc>? ORDER BY l.start_utc LIMIT 1",
        sid, tu.iso(tu.now_utc()),
    )


async def ensure_lessons(days: int = 21) -> None:
    now = tu.now_utc()
    today = now.astimezone(cfg.tz).date()
    slots = await db.rows(
        "SELECT sl.* FROM slots sl JOIN students s ON s.id=sl.student_id WHERE sl.active=1 AND s.active=1"
    )
    batch = []
    for sl in slots:
        hh, mm = map(int, sl["hhmm"].split(":"))
        for d in range(days + 1):
            day = today + timedelta(days=d)
            if day.weekday() != sl["weekday"]:
                continue
            start = datetime.combine(day, time(hh, mm), tzinfo=cfg.tz)
            if start <= now:
                continue
            batch.append((sl["student_id"], sl["id"], tu.iso(start), tu.iso(start), sl["duration"], tu.iso(now)))
    if batch:
        await db.run_many(
            "INSERT OR IGNORE INTO lessons(student_id, slot_id, occurrence, start_utc, duration, created_at) "
            "VALUES(?,?,?,?,?,?)",
            batch,
        )


def slot_local(sl, tz) -> tuple[int, str]:
    hh, mm = map(int, sl["hhmm"].split(":"))
    today = tu.now_utc().astimezone(cfg.tz).date()
    day = today + timedelta(days=(sl["weekday"] - today.weekday()) % 7)
    local = datetime.combine(day, time(hh, mm), tzinfo=cfg.tz).astimezone(tz)
    return local.weekday(), local.strftime("%H:%M")


async def mark_done(lid: int) -> int:
    l = await lesson(lid)
    price = lesson_price(l["rate"], l["duration"])
    start = tu.parse_iso(l["start_utc"])
    await db.run("DELETE FROM ledger WHERE lesson_id=?", lid)
    await db.run("UPDATE lessons SET status='done', price=?, billed=0 WHERE id=?", price, lid)
    if price:
        await db.run(
            "INSERT INTO ledger(student_id, amount, kind, lesson_id, note, created_at) VALUES(?,?,?,?,?,?)",
            l["student_id"], price, "lesson", lid, f"занятие {tu.fmt_short(start, cfg.tz)}", tu.iso(tu.now_utc()),
        )
    return price


async def refund(lid: int) -> int:
    amount = await db.val("SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE lesson_id=?", lid, default=0)
    await db.run("DELETE FROM ledger WHERE lesson_id=?", lid)
    return amount


_tutor_link: str | None = None


async def tutor_link(bot: Bot) -> str | None:
    global _tutor_link
    if _tutor_link:
        return _tutor_link
    for uid in sorted(cfg.admin_ids):
        try:
            chat = await bot.get_chat(uid)
        except Exception:
            continue
        if chat.username:
            _tutor_link = f"https://t.me/{chat.username}"
            return _tutor_link
    return None


async def hw(hid: int):
    return await db.one(HW_SQL + " WHERE hw.id=?", hid)


async def hw_items(hid: int, kind: str, rnd: int | None = None) -> list:
    if rnd is None:
        return await db.rows("SELECT * FROM hw_items WHERE hw_id=? AND kind=? ORDER BY id", hid, kind)
    return await db.rows("SELECT * FROM hw_items WHERE hw_id=? AND kind=? AND round=? ORDER BY id", hid, kind, rnd)


def hw_state(row) -> str:
    if row["status"] in ("assigned", "redo") and row["deadline_utc"]:
        if tu.parse_iso(row["deadline_utc"]) < tu.now_utc():
            return "overdue"
    return row["status"]


def deadline_text(row, tz) -> str:
    if not row["deadline_utc"]:
        return "без срока"
    return tu.fmt_dt(tu.parse_iso(row["deadline_utc"]), tz)


async def card_text(sid: int) -> str:
    s = await student(sid)
    lines = [f"👤 <b>{h(s['name'])}</b>" + ("" if s["active"] else " (в архиве)")]
    meta = " · ".join(x for x in [f"{s['grade']} класс" if s["grade"] else None, s["subject"]] if x)
    if meta:
        lines.append(h(meta))
    lines.append(f"Ставка: {rub(s['rate'])} за 60 мин")
    if s["tz"] != cfg.tz.key:
        lines.append(f"Часовой пояс: {tu.tz_label(s['tz'])}")
    slots = await db.rows("SELECT * FROM slots WHERE student_id=? AND active=1 ORDER BY weekday, hhmm", sid)
    if slots:
        lines.append("Расписание: " + ", ".join(f"{tu.WD[x['weekday']]} {x['hhmm']} ({x['duration']} мин)" for x in slots))
    else:
        lines.append("Расписание: не задано")
    nl = await next_lesson(sid)
    if nl:
        lines.append(f"Ближайшее: {tu.fmt_dt(tu.parse_iso(nl['start_utc']), cfg.tz)}")
    lines.append(balance_line(await balance(sid)))
    ms = await members(sid)
    st = [who(m) for m in ms if m["role"] == "student"]
    pr = [who(m) for m in ms if m["role"] == "parent"]
    lines.append("Ученик в боте: " + (h(", ".join(st)) if st else "не подключён"))
    lines.append("Родители в боте: " + (h(", ".join(pr)) if pr else "не подключены"))
    active = await db.val(
        "SELECT COUNT(*) FROM homework WHERE student_id=? AND status IN ('assigned','redo')", sid, default=0
    )
    review = await db.val("SELECT COUNT(*) FROM homework WHERE student_id=? AND status='submitted'", sid, default=0)
    if active or review:
        lines.append(f"ДЗ: активных {active}, на проверке {review}")
    return "\n".join(lines)


def review_kb(hid: int):
    return ikb(
        [btn("✅ Принять", "hwacc", hid), btn("🔁 На доработку", "hwredo", hid)],
        [btn("📎 Задание", "hwtask", hid)],
    )


async def send_review(bot: Bot, chat_id: int, hid: int) -> None:
    row = await hw(hid)
    items = await hw_items(hid, "solution", row["round"])
    head = f"📥 <b>{h(row['name'])}</b> сдал(а) ДЗ «{h(row['title'])}»"
    if row["round"] > 1:
        head += f" (попытка {row['round']})"
    if row["deadline_utc"] and row["submitted_at"] and row["submitted_at"] > row["deadline_utc"]:
        head += "\n⚠️ Сдано после срока"
    await send(bot, chat_id, head)
    await send_items(bot, chat_id, items)
    await send(bot, chat_id, "Проверь и выбери 👇", reply_markup=review_kb(hid))


async def show_task(bot: Bot, chat_id: int, hid: int, header: str | None = None) -> None:
    row = await hw(hid)
    await send(bot, chat_id, header or f"📎 Задание «{h(row['title'])}»")
    await send_items(bot, chat_id, await hw_items(hid, "task"))


async def deliver_hw(bot: Bot, hid: int) -> bool:
    row = await hw(hid)
    ids = await member_ids(row["student_id"], "student")
    stz = tu.zone(row["tz"])
    ok = False
    for uid in ids:
        head = f"📚 <b>Новое домашнее задание</b>: «{h(row['title'])}»\nСрок: {deadline_text(row, stz)}"
        if not await send(bot, uid, head):
            continue
        await send_items(bot, uid, await hw_items(hid, "task"))
        await send(bot, uid, "Когда решишь, жми кнопку 👇", reply_markup=ikb([btn("📤 Сдать решение", "hws", hid)]))
        ok = True
    return ok


async def send_backup(bot: Bot, chat_id: int, caption: str) -> bool:
    stamp = tu.now_utc().astimezone(cfg.tz).strftime("%Y-%m-%d_%H-%M")
    path = os.path.join(tempfile.gettempdir(), f"tutorbot_{stamp}.db")
    try:
        await db.snapshot(path)
        await bot.send_document(chat_id, FSInputFile(path, filename=f"bot_{stamp}.db"), caption=caption)
        return True
    except Exception as e:
        await send(bot, chat_id, f"⚠️ Не получилось сделать бэкап: {h(e)}")
        return False
    finally:
        if os.path.exists(path):
            os.remove(path)


def pay_request_kb(pid: int, amount: int):
    return ikb(
        [btn(f"✅ Подтвердить {rub(amount)}", "pay_ok", pid)],
        [btn("✏️ Другая сумма", "pay_fix", pid), btn("❌ Отклонить", "pay_no", pid)],
    )


async def send_pay_request(bot: Bot, chat_id: int, pid: int) -> None:
    pr = await db.one(
        "SELECT p.*, s.name FROM pay_requests p JOIN students s ON s.id=p.student_id WHERE p.id=?", pid
    )
    caption = (
        f"🧾 Оплата: <b>{h(pr['name'])}</b>, {rub(pr['amount'])}\n"
        f"От: {h(await who_tg(pr['tg_id']))}\n"
        f"Долг до подтверждения: {rub(max(await balance(pr['student_id']), 0))}"
    )
    if not pr["file_id"]:
        caption += "\nЧек не приложен"
    kw = {"reply_markup": pay_request_kb(pid, pr["amount"])} if pr["status"] == "pending" else {}
    await send_file(bot, chat_id, pr["file_id"], pr["file_type"], caption, **kw)
