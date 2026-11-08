from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.filters import Filter
from aiogram.types import TelegramObject, User

from . import db
from .config import cfg

VIEW_AS: dict[int, str] = {}


async def memberships(uid: int) -> list:
    return await db.rows(
        "SELECT m.student_id, m.role, s.name, s.tz FROM members m JOIN students s ON s.id=m.student_id "
        "WHERE m.tg_id=? AND s.active=1 ORDER BY s.name",
        uid,
    )


async def resolve(uid: int) -> tuple[str | None, list]:
    ms = await memberships(uid)
    if uid in cfg.admin_ids:
        view = VIEW_AS.get(uid)
        sel = [m for m in ms if m["role"] == view] if view else []
        if sel:
            return view, sel
        return "admin", ms
    for role in ("parent", "student"):
        sel = [m for m in ms if m["role"] == role]
        if sel:
            return role, sel
    return None, []


class RoleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        role, ms = await resolve(user.id) if user else (None, [])
        data["role"] = role
        data["ms"] = ms
        return await handler(event, data)


class Role(Filter):
    def __init__(self, *roles: str):
        self.roles = roles

    async def __call__(self, event: TelegramObject, role: str | None = None) -> bool:
        return role in self.roles


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject, event_from_user: User | None = None) -> bool:
        return bool(event_from_user) and event_from_user.id in cfg.admin_ids
