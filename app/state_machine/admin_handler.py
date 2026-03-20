"""
Admin State Handler - ניהול תפקידים לאדמין

מאפשר לאדמין להחליף תפקיד זמנית לצורך בדיקות וחקירת תפריטים.
שינוי התפקיד מתבצע ב-DB (user.role) עם שמירת original_role ב-context
לצורך חזרה לתפקיד ADMIN.
"""

from typing import Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.state_machine.states import AdminState
from app.state_machine.manager import StateManager
from app.state_machine.handlers import MessageResponse
from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_dispatcher import StationDispatcher
from app.core.logging import get_logger

logger = get_logger(__name__)

# מיפוי תפקידים לתצוגה בעברית
_ROLE_LABELS = {
    "sender": "שולח",
    "courier": "שליח",
    "driver": "נהג",
    "dispatcher": "סדרן",
    "station_owner": "בעל תחנה",
}

# מיפוי טקסט כפתור → תפקיד
_BUTTON_TO_ROLE = {
    "שולח": "sender",
    "שליח": "courier",
    "נהג": "driver",
    "סדרן": "dispatcher",
    "בעל תחנה": "station_owner",
}


class AdminStateHandler:
    """Handler לתפריט אדמין — החלפת תפקיד זמנית"""

    def __init__(self, db: AsyncSession, platform: str = "telegram"):
        self.db = db
        self.platform = platform
        self.state_manager = StateManager(db)

    async def handle_message(
        self, user: User, message: str, photo_file_id: str | None = None
    ) -> Tuple[MessageResponse, str]:
        """עיבוד הודעה נכנסת מאדמין"""
        platform = self.platform
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(user, message, context)

        if new_state != current_state:
            success = await self.state_manager.transition_to(
                user.id, platform, new_state, context_update
            )
            if not success:
                logger.info(
                    "כפיית מעבר מצב באדמין",
                    extra_data={
                        "user_id": user.id,
                        "platform": platform,
                        "current_state": current_state,
                        "new_state": new_state,
                    },
                )
                await self.state_manager.force_state(
                    user.id,
                    platform,
                    new_state,
                    {**context, **context_update} if context_update else context,
                )
        elif context_update:
            for key, value in context_update.items():
                await self.state_manager.update_context(user.id, platform, key, value)

        return response, new_state

    def _get_handler(self, state: str):
        """ניתוב ל-handler המתאים"""
        handlers = {
            AdminState.MENU.value: self._handle_menu,
            AdminState.SELECT_ROLE.value: self._handle_select_role,
        }
        return handlers.get(state, self._handle_menu)

    async def _handle_menu(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """תפריט אדמין ראשי"""
        text = (
            "<b>תפריט מנהל</b>\n\n"
            "ברוך הבא לתפריט הניהול.\n"
            "ניתן להחליף תפקיד כדי לחקור תפריטים של תפקידים אחרים."
        )
        keyboard = [
            ["החלף תפקיד"],
        ]
        return MessageResponse(text=text, keyboard=keyboard), AdminState.SELECT_ROLE.value, {}

    async def _handle_select_role(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """בחירת תפקיד להחלפה"""
        # חזרה לתפריט אדמין
        if "חזרה" in message:
            return await self._handle_menu(user, message, context)

        # בדיקה אם המשתמש בחר תפקיד
        selected_role = None
        for label, role_key in _BUTTON_TO_ROLE.items():
            if label in message:
                selected_role = role_key
                break

        if selected_role is None:
            # הצגת רשימת תפקידים לבחירה
            text = (
                "<b>בחירת תפקיד</b>\n\n"
                "בחרו תפקיד לצפייה בתפריט שלו:\n"
                "(התפקיד ישתנה זמנית — ניתן לחזור בכל רגע)"
            )
            keyboard = [
                ["שולח"],
                ["שליח"],
                ["נהג"],
                ["סדרן"],
                ["בעל תחנה"],
                ["חזרה"],
            ]
            return MessageResponse(text=text, keyboard=keyboard), AdminState.SELECT_ROLE.value, {}

        # החלפת תפקיד
        return await self._switch_to_role(user, selected_role, context)

    async def _switch_to_role(
        self, user: User, role_key: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """מבצע את החלפת התפקיד ב-DB"""
        role_label = _ROLE_LABELS.get(role_key, role_key)

        # שמירת נתונים מקוריים לשחזור
        original_approval_status = (
            user.approval_status.value if user.approval_status else None
        )

        # טיפול בתפקידים שדורשים תחנה
        station_id = None
        if role_key in ("dispatcher", "station_owner"):
            station = await self._get_first_active_station()
            if station is None:
                text = (
                    "אין תחנה פעילה במערכת.\n"
                    f"לא ניתן לעבור לתפקיד {role_label}.\n"
                    "יש ליצור תחנה קודם."
                )
                return (
                    MessageResponse(text=text, keyboard=[["חזרה"]]),
                    AdminState.SELECT_ROLE.value,
                    {},
                )
            station_id = station.id

            if role_key == "dispatcher":
                await self._ensure_dispatcher_association(user.id, station.id)
            elif role_key == "station_owner":
                await self._ensure_owner_association(user.id, station.id)

        # ניקוי שיוך סדרן שנוצר בהחלפה קודמת לתפקיד סדרן
        # אחרת _get_dispatcher_station ימצא את השיוך וינתב לתפריט סדרן
        # חשוב: מנטרלים רק את השיוך לתחנה שנוצרה בהחלפת תפקיד לסדרן,
        # כדי לא לפגוע בשיוכי סדרן אמיתיים שהיו קיימים לפני השימוש בהחלפת תפקידים.
        # בודקים admin_target_role == "dispatcher" כי admin_station_id נשמר
        # גם עבור station_owner — אסור לנטרל שיוך סדרן שלא נוצר בהחלפה
        if role_key != "dispatcher":
            prev_station_id = context.get("admin_station_id")
            prev_target_role = context.get("admin_target_role")
            if prev_station_id is not None and prev_target_role == "dispatcher":
                await self._deactivate_dispatcher_association(
                    user.id, prev_station_id
                )

        # שינוי תפקיד ב-DB
        role_map = {
            "sender": UserRole.SENDER,
            "courier": UserRole.COURIER,
            "driver": UserRole.DRIVER,
            "dispatcher": UserRole.SENDER,  # סדרנים מנותבים דרך station_dispatchers
            "station_owner": UserRole.STATION_OWNER,
        }
        target_role = role_map[role_key]
        user.role = target_role

        # שליח — הגדרת approval_status כ-APPROVED כדי לדלג על מסך ממתין
        if role_key == "courier":
            user.approval_status = ApprovalStatus.APPROVED

        await self.db.commit()

        context_update = {
            "original_role": "admin",
            "original_approval_status": original_approval_status,
            "admin_station_id": station_id,
            "admin_target_role": role_key,
        }

        logger.info(
            "אדמין החליף תפקיד",
            extra_data={
                "user_id": user.id,
                "target_role": role_key,
                "station_id": station_id,
            },
        )

        text = (
            f"התפקיד שונה ל<b>{role_label}</b>.\n\n"
            "שלחו <b>חזרה לאדמין</b> כדי לחזור לתפריט הניהול.\n"
            "מעביר לתפריט..."
        )

        # מחזיר מצב מיוחד שה-webhook ידע שצריך לנתב מחדש
        return (
            MessageResponse(text=text, keyboard=None),
            f"_ADMIN_SWITCH_{role_key}",
            context_update,
        )

    async def _get_first_active_station(self) -> Station | None:
        """שליפת התחנה הפעילה הראשונה"""
        result = await self.db.execute(
            select(Station)
            .where(Station.is_active.is_(True))
            .order_by(Station.id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _ensure_dispatcher_association(
        self, user_id: int, station_id: int
    ) -> None:
        """וידוא שקיים שיוך סדרן פעיל לתחנה"""
        result = await self.db.execute(
            select(StationDispatcher).where(
                StationDispatcher.user_id == user_id,
                StationDispatcher.station_id == station_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            if not existing.is_active:
                existing.is_active = True
        else:
            self.db.add(
                StationDispatcher(
                    user_id=user_id,
                    station_id=station_id,
                    is_active=True,
                )
            )
        await self.db.flush()

    async def _deactivate_dispatcher_association(
        self, user_id: int, station_id: int
    ) -> None:
        """ניטרול שיוך סדרן לתחנה ספציפית — רק השיוך שנוצר בהחלפת תפקיד אדמין"""
        from sqlalchemy import update

        await self.db.execute(
            update(StationDispatcher)
            .where(
                StationDispatcher.user_id == user_id,
                StationDispatcher.station_id == station_id,
                StationDispatcher.is_active.is_(True),
            )
            .values(is_active=False)
        )
        await self.db.flush()

    async def _ensure_owner_association(
        self, user_id: int, station_id: int
    ) -> None:
        """וידוא שקיים שיוך בעלים פעיל לתחנה"""
        result = await self.db.execute(
            select(StationOwner).where(
                StationOwner.user_id == user_id,
                StationOwner.station_id == station_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            if not existing.is_active:
                existing.is_active = True
        else:
            self.db.add(
                StationOwner(
                    user_id=user_id,
                    station_id=station_id,
                    is_active=True,
                )
            )
        await self.db.flush()
