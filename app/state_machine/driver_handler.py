"""
Driver State Handler — זרימת רישום נהג (iDriver סשן 2)

מנהל את מכונת המצבים של רישום נהג חדש:
1. REGISTER_COLLECT_NAME — שם מלא
2. REGISTER_COLLECT_BIRTH_DATE — תאריך לידה
3. REGISTER_COLLECT_VEHICLE — תיאור רכב
4. REGISTER_COLLECT_DRESS_CODE — קוד לבוש (עם הסתעפות לאימות/תפריט)
"""

from typing import Tuple
from html import escape
from sqlalchemy.ext.asyncio import AsyncSession

from app.state_machine.states import DriverState
from app.state_machine.manager import StateManager
from app.state_machine.handlers import MessageResponse
from app.db.models.user import User
from app.db.models.driver_profile import DressCode
from app.domain.services.driver_registration_service import (
    DriverRegistrationService,
    HAREDI_DRESS_CODES,
)
from app.core.exceptions import ValidationException
from app.core.logging import get_logger

logger = get_logger(__name__)

# מיפוי ערכי DressCode לטקסט עברי (לכפתורים)
DRESS_CODE_LABELS: dict[str, str] = {
    DressCode.HASSIDIC.value: "חסיד שחור לבן",
    DressCode.ULTRA_ORTHODOX.value: "חרדי שחור לבן",
    DressCode.MODERN_ORTHODOX.value: "חרדי מודרני",
    DressCode.RELIGIOUS_ELEGANT.value: "דתי אלגנט",
    DressCode.MIXED.value: "דתי מעורב",
    DressCode.SECULAR.value: "חילוני",
}

# מיפוי הפוך — מטקסט כפתור לערך enum
DRESS_CODE_BY_LABEL: dict[str, str] = {v: k for k, v in DRESS_CODE_LABELS.items()}

# מפתחות קונטקסט של רישום — מנוקים בחזרה ל-MENU
_REGISTRATION_CONTEXT_KEYS = {
    "reg_name",
    "reg_birth_date",
    "reg_age",
    "reg_vehicle",
}


class DriverStateHandler:
    """
    Handler לזרימת רישום נהג חדש.

    מטפל בשלבי הרישום (סשן 2) וב-INITIAL/NEW.
    שלבים נוספים (אימות, תפריט, הגדרות, חיפוש) יתווספו בסשנים הבאים.
    """

    def __init__(self, db: AsyncSession, platform: str = "telegram"):
        self.db = db
        self.platform = platform
        self.state_manager = StateManager(db)
        self.registration_service = DriverRegistrationService(db)

    def _is_registration_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת רישום"""
        return state.startswith("DRIVER.REGISTER.")

    async def handle_message(
        self, user: User, message: str, photo_file_id: str | None = None
    ) -> Tuple[MessageResponse, str]:
        """
        עיבוד הודעה נכנסת מנהג.

        Args:
            user: אובייקט המשתמש
            message: טקסט ההודעה
            photo_file_id: מזהה תמונה (לא בשימוש בסשן 2)

        Returns:
            tuple של (תגובה, מצב חדש)
        """
        platform = self.platform
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(user, message, context)

        # ניקוי קונטקסט רישום בחזרה ל-MENU או ל-INITIAL (ביטול)
        if (
            new_state in (DriverState.MENU.value, DriverState.INITIAL.value)
            and self._is_registration_flow_state(current_state)
        ):
            clean_context = {
                k: v for k, v in context.items() if k not in _REGISTRATION_CONTEXT_KEYS
            }
            if context_update:
                for k, v in context_update.items():
                    if k not in _REGISTRATION_CONTEXT_KEYS:
                        clean_context[k] = v
            await self.state_manager.force_state(
                user.id, platform, new_state, clean_context
            )
            return response, new_state

        if new_state != current_state:
            success = await self.state_manager.transition_to(
                user.id, platform, new_state, context_update
            )
            if not success:
                # כפיית מעבר מצב (דילוג על ולידציה)
                logger.info(
                    "כפיית מעבר מצב בנהג",
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
        """ניתוב ל-handler המתאים לפי מצב"""
        handlers = {
            DriverState.INITIAL.value: self._handle_initial,
            DriverState.NEW.value: self._handle_initial,
            DriverState.REGISTER_COLLECT_NAME.value: self._handle_collect_name,
            DriverState.REGISTER_COLLECT_BIRTH_DATE.value: self._handle_collect_birth_date,
            DriverState.REGISTER_COLLECT_VEHICLE.value: self._handle_collect_vehicle,
            DriverState.REGISTER_COLLECT_DRESS_CODE.value: self._handle_collect_dress_code,
            # מצבים שהרישום מוביל אליהם — placeholder עד שהסשנים הבאים יממשו
            DriverState.MENU.value: self._handle_post_registration_placeholder,
            DriverState.VERIFY_COLLECT_SELFIE.value: self._handle_post_registration_placeholder,
            DriverState.VERIFY_COLLECT_ID_DOCUMENT.value: self._handle_post_registration_placeholder,
            DriverState.VERIFY_PENDING_APPROVAL.value: self._handle_post_registration_placeholder,
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== שלב 0: מצב ראשוני ====================

    async def _handle_initial(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """
        מצב ראשוני — הודעת ברוכים הבאים ומעבר לאיסוף שם.
        """
        response = MessageResponse(
            text=(
                "🚗 <b>ברוך הבא ל-iDriver!</b>\n\n"
                "בכמה שלבים פשוטים נרשום אותך למערכת.\n\n"
                "📝 <b>שלב 1 מתוך 4</b>\n"
                "מה השם המלא שלך?"
            ),
        )
        return response, DriverState.REGISTER_COLLECT_NAME.value, {}

    # ==================== שלב 1: שם מלא ====================

    async def _handle_collect_name(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """איסוף ואימות שם מלא"""
        try:
            saved_name = await self.registration_service.save_name(user.id, message)
        except ValidationException as e:
            response = MessageResponse(
                text=(
                    f"❌ {e.message}\n\n"
                    "אנא הזן שם מלא תקין (עברית או אנגלית, 2-100 תווים):"
                ),
            )
            return response, DriverState.REGISTER_COLLECT_NAME.value, {}

        response = MessageResponse(
            text=(
                f"✅ שם: {escape(saved_name)}\n\n"
                "📝 <b>שלב 2 מתוך 4</b>\n"
                "מה תאריך הלידה שלך?\n"
                "הזן בפורמט: dd/mm/yyyy\n"
                "לדוגמה: 01/10/1977"
            ),
        )
        return (
            response,
            DriverState.REGISTER_COLLECT_BIRTH_DATE.value,
            {"reg_name": saved_name},
        )

    # ==================== שלב 2: תאריך לידה ====================

    async def _handle_collect_birth_date(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """איסוף ואימות תאריך לידה"""
        try:
            birth_date, age = await self.registration_service.save_birth_date(
                user.id, message
            )
        except ValidationException as e:
            response = MessageResponse(
                text=(
                    f"❌ {e.message}\n\n"
                    "אנא הזן תאריך לידה בפורמט dd/mm/yyyy\n"
                    "לדוגמה: 01/10/1977"
                ),
            )
            return response, DriverState.REGISTER_COLLECT_BIRTH_DATE.value, {}

        reg_name = context.get("reg_name", user.full_name or user.name or "לא צוין")

        response = MessageResponse(
            text=(
                f"✅ שם: {escape(reg_name)}\n"
                f"✅ גיל: {age}\n\n"
                "📝 <b>שלב 3 מתוך 4</b>\n"
                "מה סוג הרכב שלך ושנת ייצור?\n"
                'לדוגמה: "סיינה 2025 חדישה"'
            ),
        )
        return (
            response,
            DriverState.REGISTER_COLLECT_VEHICLE.value,
            {
                "reg_birth_date": birth_date.isoformat(),
                "reg_age": str(age),
            },
        )

    # ==================== שלב 3: תיאור רכב ====================

    async def _handle_collect_vehicle(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """איסוף ואימות תיאור רכב"""
        try:
            saved_vehicle = await self.registration_service.save_vehicle(
                user.id, message
            )
        except ValidationException as e:
            response = MessageResponse(
                text=(
                    f"❌ {e.message}\n\n"
                    "אנא הזן תיאור רכב תקין (עד 200 תווים):"
                ),
            )
            return response, DriverState.REGISTER_COLLECT_VEHICLE.value, {}

        # בניית כפתורי קוד לבוש
        keyboard = [
            [DRESS_CODE_LABELS[DressCode.HASSIDIC.value]],
            [DRESS_CODE_LABELS[DressCode.ULTRA_ORTHODOX.value]],
            [DRESS_CODE_LABELS[DressCode.MODERN_ORTHODOX.value]],
            [DRESS_CODE_LABELS[DressCode.RELIGIOUS_ELEGANT.value]],
            [DRESS_CODE_LABELS[DressCode.MIXED.value]],
            [DRESS_CODE_LABELS[DressCode.SECULAR.value]],
            ["❌ ביטול"],
        ]

        response = MessageResponse(
            text=(
                f"✅ רכב: {escape(saved_vehicle)}\n\n"
                "📝 <b>שלב 4 מתוך 4</b>\n"
                "בחר את קוד הלבוש שלך:"
            ),
            keyboard=keyboard,
        )
        return (
            response,
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            {"reg_vehicle": saved_vehicle},
        )

    # ==================== שלב 4: קוד לבוש ====================

    async def _handle_collect_dress_code(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """איסוף קוד לבוש וניתוב (אימות/תפריט)"""
        msg = message.strip()

        # ביטול
        if "ביטול" in msg:
            response = MessageResponse(
                text="❌ הרישום בוטל.\nלחזרה לתחילת הרישום, שלח כל הודעה.",
            )
            return response, DriverState.INITIAL.value, {}

        # מיפוי מטקסט כפתור לערך enum
        dress_code_value = DRESS_CODE_BY_LABEL.get(msg)
        if not dress_code_value:
            # ניסיון נוסף — ערך enum ישיר (למקרה שהמשתמש שלח ערך ולא טקסט כפתור)
            valid_values = {e.value for e in DressCode}
            if msg in valid_values:
                dress_code_value = msg

        if not dress_code_value:
            keyboard = [
                [DRESS_CODE_LABELS[DressCode.HASSIDIC.value]],
                [DRESS_CODE_LABELS[DressCode.ULTRA_ORTHODOX.value]],
                [DRESS_CODE_LABELS[DressCode.MODERN_ORTHODOX.value]],
                [DRESS_CODE_LABELS[DressCode.RELIGIOUS_ELEGANT.value]],
                [DRESS_CODE_LABELS[DressCode.MIXED.value]],
                [DRESS_CODE_LABELS[DressCode.SECULAR.value]],
                ["❌ ביטול"],
            ]
            response = MessageResponse(
                text="❌ בחירה לא תקינה. אנא בחר מהרשימה:",
                keyboard=keyboard,
            )
            return response, DriverState.REGISTER_COLLECT_DRESS_CODE.value, {}

        try:
            saved_code, needs_verification = (
                await self.registration_service.save_dress_code(user.id, dress_code_value)
            )
        except ValidationException as e:
            response = MessageResponse(text=f"❌ {e.message}")
            return response, DriverState.REGISTER_COLLECT_DRESS_CODE.value, {}

        dress_code_label = DRESS_CODE_LABELS.get(saved_code, saved_code)
        reg_name = context.get("reg_name", user.full_name or user.name or "לא צוין")
        reg_age = context.get("reg_age", "")
        reg_vehicle = context.get("reg_vehicle", "")

        if needs_verification:
            # זרם חרדי → מעבר לאימות (סשן 3)
            response = MessageResponse(
                text=(
                    "✅ <b>פרטי הרישום נשמרו!</b>\n\n"
                    f"👤 שם: {escape(reg_name)}\n"
                    f"🎂 גיל: {escape(reg_age)}\n"
                    f"🚗 רכב: {escape(reg_vehicle)}\n"
                    f"👔 לבוש: {escape(dress_code_label)}\n\n"
                    "⏳ נדרש שלב אימות נוסף.\n"
                    "המערכת תמשיך לשלב האימות..."
                ),
            )
            return response, DriverState.VERIFY_COLLECT_SELFIE.value, {}

        # זרם רגיל → מעבר לתפריט ראשי
        response = MessageResponse(
            text=(
                "✅ <b>הרישום הושלם בהצלחה!</b>\n\n"
                f"👤 שם: {escape(reg_name)}\n"
                f"🎂 גיל: {escape(reg_age)}\n"
                f"🚗 רכב: {escape(reg_vehicle)}\n"
                f"👔 לבוש: {escape(dress_code_label)}\n\n"
                "🎉 ברוך הבא ל-iDriver!\n"
                "התפריט הראשי בהקמה, נעדכן אותך בקרוב."
            ),
        )
        return response, DriverState.MENU.value, {}

    # ==================== placeholder למצבים עתידיים ====================

    async def _handle_post_registration_placeholder(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """
        Placeholder למצבים שהרישום מוביל אליהם (MENU, VERIFY).
        שומר על המצב הנוכחי ומציג הודעה — מונע חזרה לרישום.
        יוחלף בסשנים הבאים.
        """
        current_state = await self.state_manager.get_current_state(
            user.id, self.platform
        )
        response = MessageResponse(
            text="🚗 המערכת בהקמה, נעדכן אותך בקרוב.",
        )
        return response, current_state, {}

    # ==================== fallback ====================

    async def _handle_unknown(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """
        מצב לא מוכר — שומר על המצב הנוכחי ומציג הודעה.
        לא מאפס לרישום כדי לא לאבד התקדמות.
        """
        current_state = await self.state_manager.get_current_state(
            user.id, self.platform
        )
        logger.warning(
            "מצב לא מוכר ב-DriverStateHandler",
            extra_data={
                "user_id": user.id,
                "platform": self.platform,
                "state": current_state,
            },
        )
        response = MessageResponse(
            text="🚗 המערכת בהקמה, נעדכן אותך בקרוב.",
        )
        return response, current_state, {}
