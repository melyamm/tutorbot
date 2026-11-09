import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand

from app import db, scheduler, services
from app.config import cfg, ids
from app.handlers import setup
from app.roles import RoleMiddleware


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dp.message.outer_middleware(RoleMiddleware())
    dp.callback_query.outer_middleware(RoleMiddleware())
    dp.include_router(setup())
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not cfg.token:
        raise SystemExit("Не найден токен бота: задай BOT_TOKEN")
    await db.connect(cfg.db_path)
    stored = ids(await db.kv_get("admin_ids"))
    if stored:
        cfg.admin_ids.clear()
        cfg.admin_ids.update(stored)
    logging.info("DB: %s, admins: %s", cfg.db_path, sorted(cfg.admin_ids) or "ещё не назначен, первый /start станет админом")
    bot = Bot(cfg.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    services.BOT_USERNAME = me.username
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="help", description="Как пользоваться"),
            BotCommand(command="cancel", description="Отменить действие"),
        ]
    )
    dp = build_dispatcher()
    task = asyncio.create_task(scheduler.run(bot))
    logging.info("Bot @%s started", me.username)
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
