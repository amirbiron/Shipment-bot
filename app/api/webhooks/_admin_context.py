"""
עזרי admin context משותפים ל-Telegram ו-WhatsApp webhooks.

פונקציות לשמירה, שחזור והזרקת כפתור "חזרה לאדמין" כשאדמין
מחליף תפקיד זמנית.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.state_machine.manager import StateManager

_ADMIN_CONTEXT_KEYS = (
    "original_role",
    "original_approval_status",
    "admin_station_id",
    "admin_target_role",
)

_ADMIN_RETURN_BUTTON = "🔙 חזרה לאדמין"


def inject_admin_return_button(response: object) -> None:
    """הוספת כפתור 'חזרה לאדמין' לתגובה — רק אם לא קיים כבר.

    פועל על כל אובייקט עם שדה ``keyboard`` (MessageResponse ודומים).
    """
    if response.keyboard is not None:
        if any(
            _ADMIN_RETURN_BUTTON in btn
            for row in response.keyboard
            for btn in row
        ):
            return
        response.keyboard.append([_ADMIN_RETURN_BUTTON])
    else:
        response.keyboard = [[_ADMIN_RETURN_BUTTON]]


async def save_admin_context(
    user_id: int,
    state_manager: "StateManager",
    platform: str,
) -> dict:
    """שמירת מפתחות אדמין מהקונטקסט לפני ניתוב שמוחק context."""
    ctx = await state_manager.get_context(user_id, platform)
    return {k: ctx[k] for k in _ADMIN_CONTEXT_KEYS if k in ctx}


async def restore_admin_context(
    user_id: int,
    state_manager: "StateManager",
    new_state: str,
    admin_keys: dict,
    platform: str,
) -> None:
    """שחזור מפתחות אדמין אחרי ניתוב שמחק context."""
    if not admin_keys:
        return
    ctx = await state_manager.get_context(user_id, platform)
    ctx.update(admin_keys)
    await state_manager.force_state(user_id, platform, new_state, context=ctx)


async def _deactivate_admin_dispatcher(
    db: object, user_id: int, station_id: int
) -> None:
    """ניטרול שיוך סדרן שנוצר בהחלפת תפקיד אדמין בלבד."""
    from sqlalchemy import update
    from app.db.models.station_dispatcher import StationDispatcher

    await db.execute(
        update(StationDispatcher)
        .where(
            StationDispatcher.user_id == user_id,
            StationDispatcher.station_id == station_id,
            StationDispatcher.is_active.is_(True),
        )
        .values(is_active=False)
    )
    await db.flush()


async def restore_admin_role_and_route(
    user: object,
    db: object,
    state_manager: "StateManager",
    platform: str,
) -> tuple:
    """שחזור תפקיד ADMIN ומעבר ישיר לתפריט אדמין — ללא מעבר דרך _route_to_role_menu.

    מחזיר (response, new_state).
    """
    from app.db.models.user import UserRole, ApprovalStatus
    from app.state_machine.admin_handler import AdminStateHandler
    from app.state_machine.states import AdminState

    ctx = await state_manager.get_context(user.id, platform)
    original_approval = ctx.get("original_approval_status")

    # ניטרול שיוך סדרן שנוצר בהחלפת תפקיד — לפני ניקוי ה-context
    # בלי זה, השיוך נשאר פעיל ו-_get_dispatcher_station() מנתב לתפריט סדרן
    admin_station_id = ctx.get("admin_station_id")
    admin_target_role = ctx.get("admin_target_role")
    if admin_station_id is not None and admin_target_role == "dispatcher":
        await _deactivate_admin_dispatcher(db, user.id, admin_station_id)

    user.role = UserRole.ADMIN
    if original_approval is not None:
        user.approval_status = (
            ApprovalStatus(original_approval) if original_approval else None
        )
    else:
        user.approval_status = None
    await db.commit()

    await state_manager.force_state(
        user.id,
        platform,
        AdminState.MENU.value,
        context={
            "original_role": None,
            "original_approval_status": None,
            "admin_station_id": None,
            "admin_target_role": None,
        },
    )
    handler = AdminStateHandler(db, platform=platform)
    return await handler.handle_message(user, "תפריט", None)
