import html
import re

from aiogram.types import Message


def h(s) -> str:
    return html.escape(str(s if s is not None else ""))


def rub(n) -> str:
    return f"{int(n):,}".replace(",", " ") + " ₽"


def parse_int(s: str | None) -> int | None:
    digits = re.sub(r"\D", "", s or "")
    if not digits or len(digits) > 9:
        return None
    return int(digits)


def plain(s: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def item_from(m: Message) -> dict | None:
    if m.photo:
        return {"type": "photo", "file_id": m.photo[-1].file_id, "text": m.html_text if m.caption else None}
    if m.document:
        return {"type": "document", "file_id": m.document.file_id, "text": m.html_text if m.caption else None}
    if m.text:
        return {"type": "text", "file_id": None, "text": m.html_text}
    return None


def make_title(items: list[dict], fallback: str) -> str:
    for it in items:
        t = plain(it.get("text"))
        if t:
            line = t.splitlines()[0].strip()
            return line if len(line) <= 40 else line[:39].rstrip() + "…"
    return fallback


def normalize_grade(s: str) -> str | None:
    s = re.sub(r"\s*(класс|кл\.?)\s*$", "", s.strip(), flags=re.I)
    return s[:20] or None
