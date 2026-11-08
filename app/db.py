import os
import shutil
import sqlite3

import aiosqlite

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS students(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    grade TEXT,
    subject TEXT,
    rate INTEGER NOT NULL DEFAULT 0,
    tz TEXT NOT NULL DEFAULT 'Europe/Moscow',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invites(
    code TEXT PRIMARY KEY,
    student_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_by INTEGER,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS members(
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    tg_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    full_name TEXT,
    username TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(student_id, tg_id, role)
);
CREATE TABLE IF NOT EXISTS slots(
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    weekday INTEGER NOT NULL,
    hhmm TEXT NOT NULL,
    duration INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons(
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    slot_id INTEGER,
    occurrence TEXT,
    start_utc TEXT NOT NULL,
    duration INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    price INTEGER,
    remind_day INTEGER NOT NULL DEFAULT 0,
    remind_hour INTEGER NOT NULL DEFAULT 0,
    billed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(slot_id, occurrence)
);
CREATE INDEX IF NOT EXISTS ix_lessons_start ON lessons(start_utc);
CREATE INDEX IF NOT EXISTS ix_lessons_student ON lessons(student_id);
CREATE TABLE IF NOT EXISTS ledger(
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    kind TEXT NOT NULL,
    lesson_id INTEGER,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ledger_student ON ledger(student_id);
CREATE TABLE IF NOT EXISTS pay_requests(
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    tg_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    file_id TEXT,
    file_type TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS homework(
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    deadline_utc TEXT,
    status TEXT NOT NULL DEFAULT 'assigned',
    round INTEGER NOT NULL DEFAULT 0,
    remind_soon INTEGER NOT NULL DEFAULT 0,
    remind_over INTEGER NOT NULL DEFAULT 0,
    comment TEXT,
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    checked_at TEXT
);
CREATE TABLE IF NOT EXISTS hw_items(
    id INTEGER PRIMARY KEY,
    hw_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    round INTEGER NOT NULL DEFAULT 0,
    type TEXT NOT NULL,
    file_id TEXT,
    text TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv(
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

_db: aiosqlite.Connection | None = None
_path: str | None = None


async def connect(path: str) -> None:
    global _db, _path
    _path = path
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    _db = await aiosqlite.connect(path)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    await _db.commit()


async def close() -> None:
    if _db is not None:
        await _db.close()


async def one(sql: str, *args):
    async with _db.execute(sql, args) as cur:
        return await cur.fetchone()


async def rows(sql: str, *args) -> list:
    async with _db.execute(sql, args) as cur:
        return list(await cur.fetchall())


async def val(sql: str, *args, default=None):
    r = await one(sql, *args)
    if r is None or r[0] is None:
        return default
    return r[0]


async def run(sql: str, *args) -> int:
    cur = await _db.execute(sql, args)
    await _db.commit()
    return cur.lastrowid


async def run_many(sql: str, seq) -> None:
    await _db.executemany(sql, seq)
    await _db.commit()


async def snapshot(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
    await _db.execute("VACUUM INTO ?", (path,))


def inspect(path: str) -> int:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("файл повреждён")
        return con.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    except sqlite3.DatabaseError as e:
        raise ValueError("это не база бота") from e
    finally:
        con.close()


async def restore(src: str) -> None:
    await _db.commit()
    await _db.close()
    if os.path.exists(_path):
        shutil.copyfile(_path, _path + ".before_restore")
    for suffix in ("-wal", "-shm"):
        if os.path.exists(_path + suffix):
            os.remove(_path + suffix)
    shutil.copyfile(src, _path)
    await connect(_path)


async def kv_get(key: str) -> str | None:
    return await val("SELECT v FROM kv WHERE k=?", key)


async def kv_set(key: str, value: str) -> None:
    await run("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", key, value)
