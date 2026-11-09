import asyncio
import itertools
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp()
os.environ.update(
    BOT_TOKEN="123456:TEST",
    ADMIN_IDS="100",
    DB_PATH=os.path.join(TMP, "bot.db"),
    TUTOR_TZ="Europe/Moscow",
    PAY_INFO="Перевод по номеру +7 900 000-00-00 (Сбер), Илья С.",
)

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, Update

from app import db, scheduler, services
from app import timeutil as tu
from app.kb import CB
from run import build_dispatcher

ADMIN, STUDENT, PARENT, STRANGER = 100, 200, 300, 999
MSK = tu.zone("Europe/Moscow")
NOW = [datetime(2026, 11, 9, 10, 0, tzinfo=MSK).astimezone(timezone.utc)]
tu.now_utc = lambda: NOW[0]
ids = itertools.count(1)
LOG: list[dict] = []
FAILS: list[str] = []


class FakeSession(BaseSession):
    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        rec = {
            "m": name,
            "chat": getattr(method, "chat_id", None),
            "text": getattr(method, "text", None) or getattr(method, "caption", None),
            "kb": getattr(method, "reply_markup", None),
            "media": getattr(method, "media", None),
        }
        LOG.append(rec)
        if name in ("AnswerCallbackQuery", "SetMyCommands", "DeleteMessage"):
            return True
        if name == "GetChat":
            return Chat(id=int(method.chat_id), type="private", username="ilya")
        if name == "SendMediaGroup":
            return [self._msg(rec["chat"]) for _ in method.media]
        return self._msg(rec["chat"] or 1, rec["text"])

    def _msg(self, chat_id, text=None):
        return Message(message_id=next(ids), date=datetime.now(), chat=Chat(id=int(chat_id), type="private"), text=text)

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""


def user(uid, name, username=None):
    return {"id": uid, "is_bot": False, "first_name": name, "username": username}


NAMES = {ADMIN: ("Илья", "ilya"), STUDENT: ("Петя", "petya"), PARENT: ("Мама Пети", None), STRANGER: ("Хз", None)}


def upd_msg(bot, uid, text=None, photo=None, document=None, group=None):
    m = {
        "message_id": next(ids),
        "date": int(NOW[0].timestamp()),
        "chat": {"id": uid, "type": "private"},
        "from": user(uid, *NAMES[uid]),
    }
    if text is not None:
        m["text"] = text
    if photo:
        m["photo"] = [{"file_id": photo, "file_unique_id": photo + "u", "width": 10, "height": 10}]
    if document:
        m["document"] = {"file_id": document, "file_unique_id": document + "u"}
    if group:
        m["media_group_id"] = group
    return Update.model_validate({"update_id": next(ids), "message": m}, context={"bot": bot})


def upd_cb(bot, uid, data):
    return Update.model_validate(
        {
            "update_id": next(ids),
            "callback_query": {
                "id": str(next(ids)),
                "from": user(uid, *NAMES[uid]),
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": next(ids),
                    "date": int(NOW[0].timestamp()),
                    "chat": {"id": uid, "type": "private"},
                    "from": {"id": 1, "is_bot": True, "first_name": "Bot"},
                    "text": "старое сообщение",
                },
            },
        },
        context={"bot": bot},
    )


def cb(a, i=0, v=""):
    return CB(a=a, i=i, v=v).pack()


def check(cond, label):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


def sent_to(uid, since):
    return [r for r in LOG[since:] if r["chat"] == uid and r["m"] != "AnswerCallbackQuery"]


def texts(uid, since):
    return "\n".join(r["text"] or "" for r in sent_to(uid, since))


def buttons(rec):
    kb = rec["kb"]
    if kb is None or not hasattr(kb, "inline_keyboard"):
        return []
    return [b for row in kb.inline_keyboard for b in row]


def find_cb(uid, since, label_part):
    for r in reversed(sent_to(uid, since)):
        for b in buttons(r):
            if label_part in b.text:
                return b.callback_data
    return None


def set_time(dt_msk):
    NOW[0] = dt_msk.astimezone(timezone.utc)


async def main():
    await db.connect(os.environ["DB_PATH"])
    bot = Bot("123456:TEST", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML"))
    services.BOT_USERNAME = "tutor_test_bot"
    dp = build_dispatcher()

    async def say(uid, text=None, **kw):
        await dp.feed_update(bot, upd_msg(bot, uid, text, **kw))

    async def press(uid, data):
        await dp.feed_update(bot, upd_cb(bot, uid, data))

    print("1. Старт и добавление ученика")
    n = len(LOG)
    await say(STRANGER, "/start")
    check("персональной ссылке" in texts(STRANGER, n), "чужой получает подсказку про ссылку")
    n = len(LOG)
    await say(ADMIN, "/start")
    check("панель репетитора" in texts(ADMIN, n), "админ видит панель")
    await say(ADMIN, "➕ Новый ученик")
    await say(ADMIN, "Петя Иванов")
    await say(ADMIN, "9 класс")
    await say(ADMIN, "Математика, ОГЭ")
    n = len(LOG)
    await say(ADMIN, "1 500 ₽")
    s = await db.one("SELECT * FROM students WHERE id=1")
    check(s and s["name"] == "Петя Иванов" and s["grade"] == "9" and s["rate"] == 1500, "ученик сохранён корректно")
    check("Ставка: 1 500 ₽" in texts(ADMIN, n), "карточка показана")

    print("2. Инвайты")
    await press(ADMIN, cb("inv", 1, "student"))
    code_s = await db.val("SELECT code FROM invites WHERE role='student'")
    await press(ADMIN, cb("inv", 1, "parent"))
    code_p = await db.val("SELECT code FROM invites WHERE role='parent'")
    n = len(LOG)
    await say(STUDENT, f"/start {code_s}")
    await say(PARENT, f"/start {code_p}")
    check("Привет, Петя Иванов" in texts(STUDENT, n), "ученик подключён")
    check("родитель ученика" in texts(PARENT, n), "родитель подключён")
    check(texts(ADMIN, n).count("к боту") == 2, "админ получил 2 уведомления о подключении")
    n = len(LOG)
    await say(STRANGER, f"/start {code_s}")
    check("недействительна" in texts(STRANGER, n), "повторное использование ссылки запрещено")

    print("3. Регулярный слот Вт 18:00")
    await press(ADMIN, cb("slot_wd", 1, "1"))
    await say(ADMIN, "18:00")
    n = len(LOG)
    await press(ADMIN, cb("slot_dur", 1, "60"))
    cnt = await db.val("SELECT COUNT(*) FROM lessons WHERE status='planned'")
    check(cnt == 3, f"сгенерировано 3 занятия на 3 недели (есть {cnt})")
    check("по вторникам в 18:00" in texts(STUDENT, n), "ученику пришло новое расписание")
    check("Петя Иванов" in texts(PARENT, n), "родителю пришло новое расписание")

    print("4. Напоминания и автопроведение")
    set_time(datetime(2026, 11, 9, 20, 0, tzinfo=MSK))
    n = len(LOG)
    await scheduler.tick(bot)
    check("завтра в 18:00" in texts(STUDENT, n), "ученику напоминание за день")
    check("занимается завтра" in texts(PARENT, n), "родителю напоминание за день")
    n = len(LOG)
    await scheduler.tick(bot)
    check(not sent_to(STUDENT, n), "повторно не напоминает")
    set_time(datetime(2026, 11, 10, 17, 0, tzinfo=MSK))
    n = len(LOG)
    await scheduler.tick(bot)
    check("Через час" in texts(STUDENT, n) and "Через час" in texts(ADMIN, n), "напоминание за час ученику и админу")
    set_time(datetime(2026, 11, 10, 19, 1, tzinfo=MSK))
    n = len(LOG)
    await scheduler.tick(bot)
    check("отмечено проведённым" in texts(ADMIN, n), "занятие автоматически проведено")
    check(await services.balance(1) == 1500, "начислено 1500")
    check(not sent_to(PARENT, n), "родителю ничего не уходит после занятия")
    set_time(datetime(2026, 11, 10, 20, 31, tzinfo=MSK))
    n = len(LOG)
    await scheduler.tick(bot)
    check(not sent_to(PARENT, n), "и через час тоже никакого автосчёта")

    print("5. Оплата родителем")
    n = len(LOG)
    await say(PARENT, "💳 Оплата")
    check("К оплате" in texts(PARENT, n), "раздел оплаты")
    await press(PARENT, cb("pay_start", 1))
    await press(PARENT, cb("pp_amt", 1, "1500"))
    n = len(LOG)
    await say(PARENT, photo="receipt1")
    photos = [r for r in sent_to(ADMIN, n) if r["m"] == "SendPhoto"]
    check(photos and "Оплата" in photos[0]["text"], "админу пришёл чек с кнопками")
    pid = await db.val("SELECT id FROM pay_requests")
    n = len(LOG)
    await press(ADMIN, cb("pay_ok", pid))
    check(await services.balance(1) == 0, "после подтверждения долг 0")
    check("подтверждена" in texts(PARENT, n), "родителю пришло подтверждение")
    n = len(LOG)
    await press(ADMIN, cb("pay_ok", pid))
    check(await services.balance(1) == 0 and "уже обработана" in texts(ADMIN, n), "двойное подтверждение не списывает дважды")

    print("6. Выдача ДЗ")
    await press(ADMIN, cb("hw_for", 1))
    await say(ADMIN, "Квадратные уравнения\n1) x² - 5x + 6 = 0\n2) 2x² + x - 1 = 0")
    await say(ADMIN, photo="t1")
    await say(ADMIN, photo="t2", group="g1")
    await say(ADMIN, photo="t3", group="g1")
    await say(ADMIN, document="t4.pdf")
    check("Готово" in str([b.text for b in buttons(LOG[-1])]), "под «Принял» есть инлайн-кнопка «Готово»")
    await press(ADMIN, cb("col_done"))
    n = len(LOG)
    await press(ADMIN, cb("dl", 0, "2"))
    hw = await db.one("SELECT * FROM homework WHERE id=1")
    check(hw and hw["title"] == "Квадратные уравнения", "название взято из первой строки")
    items = await db.val("SELECT COUNT(*) FROM hw_items WHERE hw_id=1 AND kind='task'")
    check(items == 5, f"сохранено 5 материалов (есть {items})")
    groups = [r for r in sent_to(STUDENT, n) if r["m"] == "SendMediaGroup"]
    check(groups and len(groups[0]["media"]) == 3, "3 фото ушли одним альбомом")
    check(any(r["m"] == "SendDocument" for r in sent_to(STUDENT, n)), "PDF ушёл документом")
    check("Новое домашнее задание" in texts(PARENT, n), "родителю сообщили о ДЗ")
    check("Чт 12 ноября, 23:59" in texts(ADMIN, n), "срок через 2 дня 23:59")

    print("7. Сдача и проверка ДЗ")
    n = len(LOG)
    await say(STUDENT, "📚 Домашка")
    check("Квадратные уравнения" in texts(STUDENT, n), "ученик видит ДЗ")
    await press(STUDENT, cb("sh", 1))
    await press(STUDENT, cb("hws", 1))
    n = len(LOG)
    await say(STUDENT, "✅ Готово")
    check("Сначала пришли решение" in texts(STUDENT, n), "пустую сдачу не принимает")
    await say(STUDENT, photo="s1")
    n = len(LOG)
    await say(STUDENT, "✅ Готово")
    check("на проверку" in texts(STUDENT, n), "ученик сдал")
    check("сдал(а) ДЗ" in texts(ADMIN, n), "админу пришло решение")
    await press(ADMIN, cb("hwredo", 1))
    n = len(LOG)
    await say(ADMIN, "Во 2-м проверь дискриминант")
    check("доработать" in texts(STUDENT, n) and "дискриминант" in texts(STUDENT, n), "ученик получил доработку с комментарием")
    check(not sent_to(PARENT, n), "родителя не дёргаем на доработке")
    await press(STUDENT, cb("hws", 1))
    await say(STUDENT, photo="s2")
    n = len(LOG)
    await press(STUDENT, cb("col_done"))
    check("попытка 2" in texts(ADMIN, n), "вторая попытка помечена")
    await press(ADMIN, cb("hwacc", 1))
    n = len(LOG)
    await press(ADMIN, cb("rv_nc"))
    check("принято" in texts(STUDENT, n) and "принято" in texts(PARENT, n), "принято, ученик и родитель в курсе")
    n = len(LOG)
    await press(STUDENT, cb("hws", 1))
    check(any("уже принято" in str(r) for r in LOG[n:]), "сдать принятое ДЗ нельзя")

    print("8. Отмены")
    await press(ADMIN, cb("oo", 1))
    await say(ADMIN, "11.11 21:00")
    await press(ADMIN, cb("oo_dur", 1, "90"))
    oo = await db.one("SELECT * FROM lessons WHERE slot_id IS NULL")
    check(oo is not None and oo["duration"] == 90, "разовое занятие создано")
    set_time(datetime(2026, 11, 11, 18, 30, tzinfo=MSK))
    n = len(LOG)
    await press(STUDENT, cb("mc", oo["id"]))
    urls = [b.url for r in sent_to(STUDENT, n) for b in buttons(r) if b.url]
    check(urls == ["https://t.me/ilya"], "ученику кнопка «Написать репетитору» со ссылкой")
    check((await db.one("SELECT status FROM lessons WHERE id=?", oo["id"]))["status"] == "planned", "занятие само не отменяется")
    admin_btns = [b.text for r in sent_to(ADMIN, n) for b in buttons(r)]
    check("не сможет прийти" in texts(ADMIN, n) and "🔁 Перенести" in admin_btns and "❌ Отменить" in admin_btns, "админу уведомление с кнопками")
    check(not sent_to(PARENT, n), "родителя не дёргаем")
    n = len(LOG)
    await press(STUDENT, cb("mc", oo["id"]))
    check(not sent_to(ADMIN, n), "повторное нажатие не спамит репетитора")
    n = len(LOG)
    await press(ADMIN, cb("ls_cancel", oo["id"]))
    check("отменено репетитором" in texts(STUDENT, n) and "отменено репетитором" in texts(PARENT, n), "отмена репетитором доходит до семьи")
    l17 = await db.one("SELECT * FROM lessons WHERE status='planned' ORDER BY start_utc LIMIT 1")
    n = len(LOG)
    await press(PARENT, cb("mc", l17["id"]))
    check("напишите" in texts(PARENT, n), "родителю вежливое «напишите»")
    check("Родитель" in texts(ADMIN, n), "админ видит, что написал родитель")
    await press(ADMIN, cb("ls_cancel", l17["id"]))
    n = len(LOG)
    await press(STRANGER, cb("mc", l17["id"]))
    check(not sent_to(ADMIN, n) and not sent_to(STRANGER, n), "чужой ничего не может")

    print("9. Перенос и восстановление")
    await press(ADMIN, cb("ls_restore", l17["id"]))
    check((await db.one("SELECT status FROM lessons WHERE id=?", l17["id"]))["status"] == "planned", "занятие возвращено")
    await press(ADMIN, cb("ls_move", l17["id"]))
    n = len(LOG)
    await say(ADMIN, "18.11 16:30")
    check("перенесено" in texts(STUDENT, n) and "Ср 18 ноября, 16:30" in texts(STUDENT, n), "ученик узнал о переносе")
    await scheduler.tick(bot)
    dup = await db.val("SELECT COUNT(*) FROM lessons WHERE slot_id=? AND occurrence=?", l17["slot_id"], l17["occurrence"])
    check(dup == 1, "перенесённое занятие не задублировалось генератором")

    print("10. Просрочка ДЗ")
    await press(ADMIN, cb("hw_for", 1))
    await say(ADMIN, "Текстовые задачи, №1-10")
    await say(ADMIN, "✅ Готово")
    await say(ADMIN, "12.11 20:00")
    set_time(datetime(2026, 11, 12, 20, 5, tzinfo=MSK))
    n = len(LOG)
    await scheduler.tick(bot)
    check("прошёл" in texts(STUDENT, n), "ученику о просрочке")
    check("не сдал(а)" in texts(PARENT, n), "родителю о просрочке")
    check("просрочил" in texts(ADMIN, n), "админу о просрочке")

    print("11. Утренняя сводка")
    set_time(datetime(2026, 11, 18, 9, 5, tzinfo=MSK))
    n = len(LOG)
    await scheduler.tick(bot)
    check("Доброе утро" in texts(ADMIN, n), "сводка пришла")
    n = len(LOG)
    await scheduler.tick(bot)
    check("Доброе утро" not in texts(ADMIN, n), "сводка один раз в день")

    print("12. Админские экраны")
    for t in ["👥 Ученики", "📅 Расписание", "📚 Выдать ДЗ", "📥 На проверке", "💰 Финансы", "/help"]:
        n = len(LOG)
        await say(ADMIN, t)
        check(bool(sent_to(ADMIN, n)), f"экран «{t}»")
    for data in [
        cb("st", 1), cb("ops", 1), cb("sched", 0, ""), cb("sched", -1), cb("hw_hist", 1), cb("hwa", 1), cb("hwa", 2),
        cb("hwtask", 1), cb("hwsol", 1), cb("st_edit", 1), cb("st_tz", 1), cb("st_tzset", 1, "3"), cb("slots", 1),
        cb("st_arch"), cb("st_list"), cb("money"), cb("pay_list"), cb("ls", l17["id"]),
    ]:
        n = len(LOG)
        await press(ADMIN, data)
        check(bool(LOG[n:]), f"кнопка {data}")
    st = await db.one("SELECT tz FROM students WHERE id=1")
    check(st["tz"] == "Asia/Yekaterinburg", "часовой пояс сменился")
    n = len(LOG)
    await say(STUDENT, "📅 Занятия")
    check("18:30" in texts(STUDENT, n), "ученик видит время в своём поясе (16:30 МСК = 18:30 ЕКБ)")

    print("13. Ручная оплата, начисление, напоминание должникам")
    await press(ADMIN, cb("pay_add", 1, "+"))
    await say(ADMIN, "3000")
    check(await services.balance(1) == 3000, "начислено вручную 3000")
    n = len(LOG)
    await press(ADMIN, cb("money_rs"))
    check("Напоминаю об оплате" in texts(PARENT, n), "родителю напоминание о долге")
    await press(ADMIN, cb("pay_add", 1, "-"))
    n = len(LOG)
    await press(ADMIN, cb("pay_add_q", 1, "3000"))
    check(await services.balance(1) == 0, "оплата одной кнопкой")
    check(not sent_to(PARENT, n), "родителю ничего не приходит при ручной оплате")

    print("14. Режим просмотра для админа")
    await press(ADMIN, cb("inv", 1, "student"))
    code = await db.val("SELECT code FROM invites WHERE used_by IS NULL ORDER BY created_at DESC LIMIT 1")
    await say(ADMIN, f"/start {code}")
    n = len(LOG)
    await say(ADMIN, "/mode")
    check("ученик" in texts(ADMIN, n), "переключился в режим ученика")
    n = len(LOG)
    await say(ADMIN, "📚 Домашка")
    check("Домашка" in texts(ADMIN, n), "в режиме ученика видит домашку")
    await say(ADMIN, "/mode")
    n = len(LOG)
    await say(ADMIN, "/unlink")
    check(await db.val("SELECT COUNT(*) FROM members WHERE tg_id=?", ADMIN) == 0, "unlink работает")

    print("15. Отмена ввода и мусор")
    await say(ADMIN, "➕ Новый ученик")
    n = len(LOG)
    await say(ADMIN, "✖️ Отмена")
    check("Отменено" in texts(ADMIN, n), "отмена ввода")
    n = len(LOG)
    await say(PARENT, "привет")
    check("меню" in texts(PARENT, n), "мусор родителя: подсказка меню")
    n = len(LOG)
    await press(PARENT, cb("st", 1))
    check(not any("Ставка" in (r["text"] or "") for r in LOG[n:]), "родитель не может открыть карточку админа")

    print("16. Бэкапы")
    n = len(LOG)
    await say(ADMIN, "/backup")
    docs = [r for r in sent_to(ADMIN, n) if r["m"] == "SendDocument"]
    check(docs and "Бэкап" in docs[0]["text"], "/backup присылает файл базы")
    n = len(LOG)
    await say(PARENT, "/backup")
    check(not any(r["m"] == "SendDocument" for r in LOG[n:]), "родитель не может получить бэкап")
    set_time(datetime(2026, 11, 22, 21, 10, tzinfo=MSK))
    n = len(LOG)
    await scheduler.tick(bot)
    check(any(r["m"] == "SendDocument" for r in sent_to(ADMIN, n)), "воскресный автобэкап пришёл")
    n = len(LOG)
    await scheduler.tick(bot)
    check(not any(r["m"] == "SendDocument" for r in sent_to(ADMIN, n)), "автобэкап один раз")

    print("17. Восстановление базы")
    snap = os.path.join(TMP, "snap.db")
    await db.snapshot(snap)
    junk = os.path.join(TMP, "junk.db")
    with open(junk, "w") as f:
        f.write("not a db")
    src = {"good": snap, "bad": junk}

    async def fake_download(file, destination=None, **kw):
        import shutil
        shutil.copyfile(src[file], destination)

    bot.download = fake_download
    await say(ADMIN, "/restore")
    n = len(LOG)
    await say(ADMIN, document="bad")
    check("Не подходит" in texts(ADMIN, n), "мусорный файл отвергнут")
    await db.run("DELETE FROM students")
    n = len(LOG)
    await say(ADMIN, document="good")
    check("В файле учеников: 1. Сейчас в боте: 0" in texts(ADMIN, n), "показывает, что в файле")
    n = len(LOG)
    await press(ADMIN, cb("rst_yes"))
    check("База восстановлена" in texts(ADMIN, n), "база восстановлена")
    check(await db.val("SELECT COUNT(*) FROM students") == 1, "ученик вернулся из бэкапа")
    check(os.path.exists(os.environ["DB_PATH"] + ".before_restore"), "старая база сохранена рядом")
    n = len(LOG)
    await say(STUDENT, "📅 Занятия")
    check("занятия" in texts(STUDENT, n).lower(), "ученик после восстановления работает")

    print("18. Архив")
    await press(ADMIN, cb("st_arch_yes", 1))
    check(await db.val("SELECT COUNT(*) FROM lessons WHERE status='planned'") == 0, "будущие занятия удалены")
    n = len(LOG)
    await say(STUDENT, "📅 Занятия")
    check("ссылке" in texts(STUDENT, n), "архивный ученик отключён от меню")

    print("18б. Корректировка финансов")
    await db.run("UPDATE students SET active=1 WHERE id=1")
    base = await services.balance(1)
    await press(ADMIN, cb("pay_add", 1, "+"))
    await say(ADMIN, "5000")
    check(await services.balance(1) == base + 5000, "начислено 5000")
    await press(ADMIN, cb("wo_q", 1))
    n = len(LOG)
    await press(ADMIN, cb("wo", 1))
    check(await services.balance(1) == 0 and "Списано" in texts(ADMIN, n), "долг списан")
    paid_before = await db.val("SELECT COALESCE(SUM(-amount),0) FROM ledger WHERE kind='payment'")
    wo = await db.val("SELECT COUNT(*) FROM ledger WHERE kind='writeoff'")
    check(wo == 1, "списание отдельным видом, не оплатой")
    last = await db.val("SELECT id FROM ledger WHERE kind='writeoff'")
    await press(ADMIN, cb("op_list", 1))
    await press(ADMIN, cb("op_delq", last))
    n = len(LOG)
    await press(ADMIN, cb("op_del", last))
    check(await services.balance(1) == base + 5000 and "Удалено" in texts(ADMIN, n), "удаление операции возвращает долг")
    n = len(LOG)
    await say(ADMIN, "💰 Финансы")
    before = texts(ADMIN, n)
    await press(ADMIN, cb("fin_cfg"))
    n = len(LOG)
    await press(ADMIN, cb("fin_since"))
    after = texts(ADMIN, n)
    check("считается с" in after and "занятий 0 на 0 ₽, получено 0 ₽" in after, "после «считать с этой минуты» месяц пустой")
    check("Должны" in after, "долги при этом остаются")
    await press(ADMIN, cb("fin_wo_q"))
    n = len(LOG)
    await press(ADMIN, cb("fin_wo"))
    check(await services.balance(1) == 0 and "Долгов нет" in texts(ADMIN, n), "списать все долги")
    n = len(LOG)
    await press(ADMIN, cb("fin_all"))
    check("за всё время" in texts(ADMIN, n) and "считается с" not in texts(ADMIN, n).split("\n\n", 1)[1], "вернуть статистику за всё время")
    await db.run("UPDATE students SET active=0 WHERE id=1")

    print("19. Передача бота")
    from app.config import cfg as _cfg
    from app import services as _sv
    VOVA = STRANGER
    n = len(LOG)
    await say(VOVA, "/transfer")
    check(not any("Передача" in (r["text"] or "") for r in sent_to(VOVA, n)), "не-админ не может /transfer")
    await say(ADMIN, "/transfer")
    await press(ADMIN, cb("tr", 0, "full"))
    code = (await db.kv_get("admin_invite")).split("|")[0]
    n = len(LOG)
    await say(ADMIN, f"/start {code}")
    check("уже репетитор" in texts(ADMIN, n) and await db.kv_get("admin_invite"), "сам себе ссылку не активирует")
    n = len(LOG)
    await say(VOVA, f"/start {code}")
    check(_cfg.admin_ids == {VOVA}, "Владимир стал единственным админом")
    check(await db.kv_get("admin_ids") == str(VOVA), "сохранено в базе")
    check("Добро пожаловать" in texts(VOVA, n), "Владимиру приветствие")
    check("Бот передан" in texts(ADMIN, n), "старому админу уведомление")
    n = len(LOG)
    await say(ADMIN, "👥 Ученики")
    check(not any("Ученики" in (r["text"] or "") for r in sent_to(ADMIN, n)), "старый админ больше не видит учеников")
    n = len(LOG)
    await say(ADMIN, f"/start {code}")
    check("недействительна" in texts(ADMIN, n), "ссылка одноразовая")
    n = len(LOG)
    await say(VOVA, "👥 Ученики")
    check(any("Новый ученик" in str([b.text for b in buttons(r)]) for r in sent_to(VOVA, n)), "Владимир видит панель учеников")
    await say(VOVA, "/transfer")
    await press(VOVA, cb("tr", 0, "add"))
    await press(VOVA, cb("tr_cancel"))
    n = len(LOG)
    code2 = "a_nope"
    await say(ADMIN, f"/start {code2}")
    check("недействительна" in texts(ADMIN, n), "отменённая/чужая ссылка не работает")
    await say(VOVA, "/transfer")
    await press(VOVA, cb("tr", 0, "add"))
    code3 = (await db.kv_get("admin_invite")).split("|")[0]
    n = len(LOG)
    await say(ADMIN, f"/start {code3}")
    check(_cfg.admin_ids == {VOVA, ADMIN}, "режим «вторым»: два админа")
    check("второй репетитор" in texts(VOVA, n), "Владимир узнал о втором")
    _sv.reset_tutor_link()
    check(await db.kv_get("tutor_contact") == str(ADMIN), "кнопка «написать» ведёт к последнему добавленному")
    await say(ADMIN, "/leave")
    n = len(LOG)
    await press(ADMIN, cb("leave_yes"))
    check(_cfg.admin_ids == {VOVA}, "/leave работает")
    check("вышел" in texts(VOVA, n), "оставшемуся сообщили")
    n = len(LOG)
    await say(VOVA, "/leave")
    check("единственный" in texts(VOVA, n), "последний админ выйти не может")

    with open(os.path.join(TMP, "transcript.txt"), "w") as f:
        for r in LOG:
            if r["m"] == "AnswerCallbackQuery":
                continue
            labels = [b.text for b in buttons(r)]
            f.write(f"[{r['m']} -> {r['chat']}] {r['text']}\n")
            if labels:
                f.write(f"    кнопки: {labels}\n")
    print(f"\nTranscript: {os.path.join(TMP, 'transcript.txt')}")
    print("ИТОГ:", "всё ок" if not FAILS else f"провалено {len(FAILS)}: {FAILS}")
    await db.close()


asyncio.run(main())
