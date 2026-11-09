from aiogram import Router

from ..roles import IsAdmin, Role
from . import admin_hw, admin_lessons, admin_menu, admin_money, admin_restore, admin_students, admin_transfer, common, fallback, member


def setup() -> Router:
    root = Router(name="root")
    root.include_router(common.router)
    admin = Router(name="admin")
    admin.message.filter(Role("admin"))
    admin.callback_query.filter(IsAdmin())
    admin.include_routers(
        admin_menu.router,
        admin_restore.router,
        admin_transfer.router,
        admin_students.router,
        admin_lessons.router,
        admin_hw.router,
        admin_money.router,
    )
    root.include_router(admin)
    root.include_router(member.router)
    root.include_router(fallback.router)
    return root
