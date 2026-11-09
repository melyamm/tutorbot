import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from . import db, services as sv, timeutil as tu
from .config import cfg
from .kb import btn, ikb
from .notify import to_admins, to_members
from .util import h, rub

log = logging.getLogger("scheduler")

_last_gen: datetime | None = None


async def run(bot: Bot) -> None:
    while True:
        try:
            await tick(bot)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(30)


async def tick(bot: Bot) -> None:
    global _last_gen
    now = tu.now_utc()
    if _last_gen is None or now - _last_gen >= timedelta(minutes=10) or now < _last_gen:
        await sv.ensure_lessons()
        _last_gen = now
    await lesson_reminders(bot, now)
    await auto_done(bot, now)
    await hw_reminders(bot, now)
    await digest(bot, now)
    await weekly_backup(bot, now)


async def weekly_backup(bot: Bot, now: datetime) -> None:
    local = now.astimezone(cfg.tz)
    if local.weekday() != 6 or local.hour < 21:
        return
    today = local.date().isoformat()
    if await db.kv_get("backup") == today:
        return
    await db.kv_set("backup", today)
    for uid in cfg.admin_ids:
        await sv.send_backup(bot, uid, "💾 Еженедельный бэкап базы. Вручную: /backup, восстановить: /restore")


async def lesson_reminders(bot: Bot, now: datetime) -> None:
    rows = await db.rows(
        sv.LESSON_SQL + " WHERE l.status='planned' AND s.active=1 AND l.start_utc>? AND l.start_utc<=? "
        "AND (l.remind_day=0 OR l.remind_hour=0) ORDER BY l.start_utc",
        tu.iso(now), tu.iso(now + timedelta(hours=24)),
    )
    for r in rows:
        start = tu.parse_iso(r["start_utc"])
        left = start - now
        stz = tu.zone(r["tz"])
        sid = r["student_id"]
        if not r["remind_day"]:
            await db.run("UPDATE lessons SET remind_day=1 WHERE id=?", r["id"])
            if left > timedelta(hours=3):
                when = f"{tu.rel_day(start, stz)} в {tu.fmt_time(start, stz)}"
                subj = f" ({h(r['subject'])})" if r["subject"] else ""
                await to_members(
                    bot, sid, "student", f"📅 Напоминаю: {when} занятие{subj}.",
                    reply_markup=ikb([btn("🙅 Не смогу", "mc", r["id"])]),
                )
                await to_members(
                    bot, sid, "parent", f"📅 Напоминание: <b>{h(r['name'])}</b> занимается {when}.",
                    reply_markup=ikb([btn("🙅 Не получится", "mc", r["id"])]),
                )
        if not r["remind_hour"] and left <= timedelta(minutes=65):
            await db.run("UPDATE lessons SET remind_hour=1 WHERE id=?", r["id"])
            mins = int(left.total_seconds() // 60)
            soon = "Через час" if mins >= 50 else f"Через {max(mins, 1)} мин"
            await to_members(bot, sid, "student", f"⏰ {soon} занятие, в {tu.fmt_time(start, stz)}.")
            await to_admins(bot, f"⏰ {soon}: <b>{h(r['name'])}</b>, {tu.fmt_time(start, cfg.tz)}")


async def auto_done(bot: Bot, now: datetime) -> None:
    rows = await db.rows(
        sv.LESSON_SQL + " WHERE l.status='planned' AND l.start_utc<=?", tu.iso(now)
    )
    for r in rows:
        start = tu.parse_iso(r["start_utc"])
        if start + timedelta(minutes=r["duration"]) > now:
            continue
        price = await sv.mark_done(r["id"])
        await to_admins(
            bot,
            f"✅ Занятие <b>{h(r['name'])}</b> {tu.fmt_short(start, cfg.tz)} отмечено проведённым"
            + (f", начислено {rub(price)}." if price else "."),
            reply_markup=ikb([btn("↩️ Не было", "ls_undo", r["id"])]),
        )


async def hw_reminders(bot: Bot, now: datetime) -> None:
    rows = await db.rows(
        sv.HW_SQL + " WHERE hw.status IN ('assigned','redo') AND hw.deadline_utc IS NOT NULL AND s.active=1 "
        "AND (hw.remind_soon=0 OR hw.remind_over=0)"
    )
    for r in rows:
        dl = tu.parse_iso(r["deadline_utc"])
        left = dl - now
        stz = tu.zone(r["tz"])
        sid = r["student_id"]
        submit = ikb([btn("📤 Сдать решение", "hws", r["id"])])
        if left <= timedelta(0) and not r["remind_over"]:
            await db.run("UPDATE homework SET remind_over=1, remind_soon=1 WHERE id=?", r["id"])
            await to_members(
                bot, sid, "student", f"⚠️ Срок сдачи ДЗ «{h(r['title'])}» прошёл. Сдай как можно скорее 🙏",
                reply_markup=submit,
            )
            await to_members(
                bot, sid, "parent",
                f"⚠️ <b>{h(r['name'])}</b> не сдал(а) домашнее задание «{h(r['title'])}» в срок ({tu.fmt_dt(dl, stz)}).",
            )
            await to_admins(bot, f"⚠️ <b>{h(r['name'])}</b> просрочил(а) ДЗ «{h(r['title'])}»")
        elif timedelta(0) < left <= timedelta(hours=24) and not r["remind_soon"]:
            created = tu.parse_iso(r["created_at"])
            await db.run("UPDATE homework SET remind_soon=1 WHERE id=?", r["id"])
            if now - created >= timedelta(hours=3):
                await to_members(
                    bot, sid, "student",
                    f"⏳ {tu.rel_day(dl, stz).capitalize()} в {tu.fmt_time(dl, stz)} срок сдачи ДЗ «{h(r['title'])}».",
                    reply_markup=submit,
                )


async def digest(bot: Bot, now: datetime) -> None:
    local = now.astimezone(cfg.tz)
    if not cfg.digest_hour <= local.hour < cfg.digest_hour + 3:
        return
    today = local.date().isoformat()
    if await db.kv_get("digest") == today:
        return
    await db.kv_set("digest", today)
    start = local.replace(hour=0, minute=0, second=0)
    end = start + timedelta(days=1)
    rows = await db.rows(
        sv.LESSON_SQL + " WHERE l.status='planned' AND l.start_utc>=? AND l.start_utc<? ORDER BY l.start_utc",
        tu.iso(start), tu.iso(end),
    )
    lines = ["☀️ <b>Доброе утро!</b>", ""]
    if rows:
        lines.append(f"Сегодня занятий: {len(rows)}")
        lines += [f"• {tu.fmt_time(tu.parse_iso(r['start_utc']), cfg.tz)} {h(r['name'])}" for r in rows]
    else:
        lines.append("Сегодня занятий нет.")
    review = await db.val("SELECT COUNT(*) FROM homework WHERE status='submitted'", default=0)
    pending = await db.val("SELECT COUNT(*) FROM pay_requests WHERE status='pending'", default=0)
    bals = await sv.balances()
    debt = sum(b for b in bals.values() if b > 0)
    extra = []
    if review:
        extra.append(f"📥 На проверке: {review}")
    if pending:
        extra.append(f"🧾 Заявок на оплату: {pending}")
    if debt:
        extra.append(f"💰 Должны всего: {rub(debt)}")
    if not rows and not extra:
        return
    if extra:
        lines.append("")
        lines += extra
    await to_admins(bot, "\n".join(lines))
