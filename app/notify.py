import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InputMediaPhoto, Message

from . import db
from .config import cfg

log = logging.getLogger("notify")


async def send(bot: Bot, chat_id: int, text: str, **kw):
    try:
        return await bot.send_message(chat_id, text, **kw)
    except TelegramAPIError as e:
        log.warning("send to %s failed: %s", chat_id, e)
        return None


async def to_admins(bot: Bot, text: str, **kw) -> None:
    for uid in cfg.admin_ids:
        await send(bot, uid, text, **kw)


async def member_ids(sid: int, role: str | None = None, exclude: int | None = None) -> list[int]:
    if role:
        rs = await db.rows("SELECT DISTINCT tg_id FROM members WHERE student_id=? AND role=?", sid, role)
    else:
        rs = await db.rows("SELECT DISTINCT tg_id FROM members WHERE student_id=?", sid)
    return [r["tg_id"] for r in rs if r["tg_id"] != exclude]


async def to_members(bot: Bot, sid: int, role: str | None, text: str, exclude: int | None = None, **kw) -> int:
    n = 0
    for uid in await member_ids(sid, role, exclude):
        if await send(bot, uid, text, **kw):
            n += 1
    return n


async def send_items(bot: Bot, chat_id: int, items) -> bool:
    buf: list = []

    async def flush():
        if not buf:
            return
        if len(buf) == 1:
            await bot.send_photo(chat_id, buf[0]["file_id"], caption=buf[0]["text"] or None)
        else:
            await bot.send_media_group(
                chat_id, [InputMediaPhoto(media=i["file_id"], caption=i["text"] or None) for i in buf]
            )
        buf.clear()

    try:
        for it in items:
            if it["type"] == "photo":
                buf.append(it)
                if len(buf) == 10:
                    await flush()
                continue
            await flush()
            if it["type"] == "document":
                await bot.send_document(chat_id, it["file_id"], caption=it["text"] or None)
            else:
                await bot.send_message(chat_id, it["text"])
        await flush()
        return True
    except TelegramAPIError as e:
        log.warning("send_items to %s failed: %s", chat_id, e)
        return False


async def send_file(bot: Bot, chat_id: int, file_id: str | None, file_type: str | None, caption: str, **kw):
    try:
        if file_id and file_type == "photo":
            return await bot.send_photo(chat_id, file_id, caption=caption, **kw)
        if file_id and file_type == "document":
            return await bot.send_document(chat_id, file_id, caption=caption, **kw)
        return await bot.send_message(chat_id, caption, **kw)
    except TelegramAPIError as e:
        log.warning("send_file to %s failed: %s", chat_id, e)
        return None


async def edit(msg: Message, text: str, markup=None) -> None:
    try:
        await msg.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        await msg.answer(text, reply_markup=markup)


async def drop_kb(msg: Message) -> None:
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass
