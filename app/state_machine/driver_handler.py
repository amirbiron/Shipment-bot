"""
Driver State Handler — זרימת רישום, אימות, תפריט והגדרות נהג (iDriver סשנים 2-4)

מנהל את מכונת המצבים של רישום נהג חדש:
1. REGISTER_COLLECT_NAME — שם מלא
2. REGISTER_COLLECT_BIRTH_DATE — תאריך לידה
3. REGISTER_COLLECT_VEHICLE — תיאור רכב
4. REGISTER_COLLECT_DRESS_CODE — קוד לבוש (עם הסתעפות לאימות/תפריט)

ואימות חרדי (סשן 3):
5. VERIFY_COLLECT_SELFIE — תמונת סלפי
6. VERIFY_COLLECT_ID_DOCUMENT — תעודת זהות
7. VERIFY_PENDING_APPROVAL — המתנה לאישור מנהל

תפריט ראשי והגדרות חיפוש (סשן 4):
8. MENU — תפריט ראשי עם סטטוס מנוי והגדרות
9. SETTINGS_VIEW — תפריט הגדרות חיפוש
10. SETTINGS_VEHICLE_TYPE — בחירת סוג רכב
11. SETTINGS_TRIP_TYPE — בחירת סוג נסיעה
12. SETTINGS_SHOW_DELIVERIES — הצגת משלוחים (כן/לא)
13. SETTINGS_UPCOMING_TIMEFRAME — מסגרת זמן
14. SETTINGS_FUTURE_ONLY_MODE — חיפוש עתידי בלבד
15. SETTINGS_START_TIME — שעת התחלה לחיפוש עתידי
"""

from datetime import time as time_type
from typing import Tuple
from html import escape
from sqlalchemy.ext.asyncio import AsyncSession

from app.state_machine.states import DriverState
from app.state_machine.manager import StateManager
from app.state_machine.handlers import MessageResponse
from app.db.models.user import User
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    VehicleCategory,
    DriverVerificationStatus,
)
from app.db.models.driver_search_settings import TripTypeFilter, UpcomingTimeframe
from app.domain.services.driver_registration_service import DriverRegistrationService
from app.domain.services.driver_verification_service import DriverVerificationService
from app.domain.services.driver_menu_service import (
    DriverMenuService,
    VEHICLE_TYPE_LABELS,
    VEHICLE_TYPE_BY_LABEL,
    TRIP_TYPE_LABELS,
    TRIP_TYPE_BY_LABEL,
    TIMEFRAME_LABELS,
    TIMEFRAME_BY_LABEL,
)
from sqlalchemy import select
from app.core.exceptions import ValidationException, NotFoundException
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

# מקלדת כפתורי קוד לבוש — משותפת לשלב 3→4 ולשגיאת בחירה בשלב 4
_DRESS_CODE_KEYBOARD = [
    [DRESS_CODE_LABELS[DressCode.HASSIDIC.value]],
    [DRESS_CODE_LABELS[DressCode.ULTRA_ORTHODOX.value]],
    [DRESS_CODE_LABELS[DressCode.MODERN_ORTHODOX.value]],
    [DRESS_CODE_LABELS[DressCode.RELIGIOUS_ELEGANT.value]],
    [DRESS_CODE_LABELS[DressCode.MIXED.value]],
    [DRESS_CODE_LABELS[DressCode.SECULAR.value]],
    ["❌ ביטול"],
]

# מפתחות קונטקסט של רישום — מנוקים בחזרה ל-MENU
_REGISTRATION_CONTEXT_KEYS = {
    "reg_name",
    "reg_birth_date",
    "reg_age",
    "reg_vehicle",
}


class DriverStateHandler:
    """
    Handler לזרימת רישום, אימות, תפריט והגדרות נהג.

    מטפל בשלבי הרישום (סשן 2), אימות חרדי (סשן 3),
    תפריט ראשי והגדרות חיפוש (סשן 4).
    שלבים נוספים (חיפוש, מנוי) יתווספו בסשנים הבאים.
    """

    def __init__(self, db: AsyncSession, platform: str = "telegram"):
        self.db = db
        self.platform = platform
        self.state_manager = StateManager(db)
        self.registration_service = DriverRegistrationService(db)
        self.verification_service = DriverVerificationService(db)
        self.menu_service = DriverMenuService(db)

    def _is_registration_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת רישום או אימות (לניקוי קונטקסט)"""
        return state.startswith(("DRIVER.REGISTER.", "DRIVER.VERIFY."))

    def _is_settings_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת הגדרות (לניקוי קונטקסט)"""
        return state.startswith("DRIVER.SETTINGS.")

    async def handle_message(
        self, user: User, message: str, photo_file_id: str | None = None
    ) -> Tuple[MessageResponse, str]:
        """
        עיבוד הודעה נכנסת מנהג.

        Args:
            user: אובייקט המשתמש
            message: טקסט ההודעה
            photo_file_id: מזהה תמונה (בשימוש בשלבי אימות — סשן 3)

        Returns:
            tuple של (תגובה, מצב חדש)
        """
        platform = self.platform
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(
            user, message, context, photo_file_id=photo_file_id
        )

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
            # אימות חרדי (סשן 3)
            DriverState.VERIFY_COLLECT_SELFIE.value: self._handle_verify_collect_selfie,
            DriverState.VERIFY_COLLECT_ID_DOCUMENT.value: self._handle_verify_collect_id_document,
            DriverState.VERIFY_PENDING_APPROVAL.value: self._handle_verify_pending_approval,
            # תפריט ראשי והגדרות חיפוש (סשן 4)
            DriverState.MENU.value: self._handle_menu,
            DriverState.SETTINGS_VIEW.value: self._handle_settings_view,
            DriverState.SETTINGS_VEHICLE_TYPE.value: self._handle_settings_vehicle_type,
            DriverState.SETTINGS_TRIP_TYPE.value: self._handle_settings_trip_type,
            DriverState.SETTINGS_SHOW_DELIVERIES.value: self._handle_settings_show_deliveries,
            DriverState.SETTINGS_UPCOMING_TIMEFRAME.value: self._handle_settings_timeframe,
            DriverState.SETTINGS_FUTURE_ONLY_MODE.value: self._handle_settings_future_only,
            DriverState.SETTINGS_START_TIME.value: self._handle_settings_start_time,
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== שלב 0: מצב ראשוני ====================

    async def _handle_initial(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        מצב ראשוני — בדיקה אם הרישום כבר הושלם.
        אם כן — מעבר לתפריט. אם לא — הודעת ברוכים הבאים ומעבר לאיסוף שם.
        """
        # בדיקה אם הנהג כבר רשום — למנוע הפעלה מחדש של רישום
        result = await self.db.execute(
            select(DriverProfile).where(DriverProfile.user_id == user.id)
        )
        profile = result.scalar_one_or_none()
        if profile and profile.is_registration_complete:
            logger.info(
                "נהג רשום ניגש ל-INITIAL — מנתב לתפריט",
                extra_data={"user_id": user.id},
            )
            return await self._build_main_menu(user)

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
        self, user: User, message: str, context: dict, **kwargs: object
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
        self, user: User, message: str, context: dict, **kwargs: object
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
        self, user: User, message: str, context: dict, **kwargs: object
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

        response = MessageResponse(
            text=(
                f"✅ רכב: {escape(saved_vehicle)}\n\n"
                "📝 <b>שלב 4 מתוך 4</b>\n"
                "בחר את קוד הלבוש שלך:"
            ),
            keyboard=_DRESS_CODE_KEYBOARD,
        )
        return (
            response,
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            {"reg_vehicle": saved_vehicle},
        )

    # ==================== שלב 4: קוד לבוש ====================

    async def _handle_collect_dress_code(
        self, user: User, message: str, context: dict, **kwargs: object
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
            response = MessageResponse(
                text="❌ בחירה לא תקינה. אנא בחר מהרשימה:",
                keyboard=_DRESS_CODE_KEYBOARD,
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

    # ==================== סשן 3: אימות חרדי ====================

    async def _handle_verify_collect_selfie(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        איסוף תמונת סלפי לאימות.

        בכניסה הראשונה (ללא photo_file_id) — מציג הנחיות.
        כשמתקבלת תמונה — שומר ועובר לשלב הבא.
        """
        photo_file_id: str | None = kwargs.get("photo_file_id")

        # ביטול
        if message and "ביטול" in message.strip():
            response = MessageResponse(
                text=(
                    "❌ האימות בוטל.\n"
                    "תוכל לחזור לאימות בכל שלב דרך התפריט."
                ),
            )
            return response, DriverState.MENU.value, {}

        # שליחת טקסט ללא תמונה — הצגת הנחיות
        if not photo_file_id:
            # בדיקה אם הנהג נדחה ומגיש מחדש
            result = await self.db.execute(
                select(DriverProfile).where(DriverProfile.user_id == user.id)
            )
            profile = result.scalar_one_or_none()
            rejection_line = ""
            if profile and profile.verification_status == DriverVerificationStatus.REJECTED.value:
                reason = profile.rejection_reason or "לא צוינה"
                rejection_line = (
                    f"\n⚠️ <b>הגשתך הקודמת נדחתה.</b>\n"
                    f"סיבה: {escape(reason)}\n"
                )

            # כרטיס נהג לא מאומת
            reg_name = context.get("reg_name", user.full_name or user.name or "לא צוין")
            reg_age = context.get("reg_age", "")
            reg_vehicle = context.get("reg_vehicle", "")
            dress_label = ""
            if profile and profile.dress_code:
                dress_label = DRESS_CODE_LABELS.get(profile.dress_code, profile.dress_code)

            response = MessageResponse(
                text=(
                    f"🕵🏼 • שם מלא - {escape(reg_name)}"
                    f" | 🔞 גיל - {escape(reg_age)}"
                    f" | 🏎 רכב - {escape(reg_vehicle)}"
                    f" | 🦓 זרם: {escape(dress_label)} - לא מאומת\n\n"
                    "➖ הנך לא מזוהה במערכת ➖\n"
                    f"{rejection_line}\n"
                    "📸 <b>שלב אימות 1 מתוך 2</b>\n"
                    "שלח תמונת סלפי עדכנית שלך.\n"
                    "ודא שהפנים ברורות ומוארות."
                ),
                keyboard=[["📸 צלם סלפי"], ["❌ ביטול"]],
            )
            return response, DriverState.VERIFY_COLLECT_SELFIE.value, {}

        # תמונה התקבלה — שמירה
        try:
            await self.verification_service.submit_selfie(user.id, photo_file_id)
        except (ValidationException, NotFoundException) as e:
            logger.error(
                "כשלון בשמירת סלפי",
                extra_data={"user_id": user.id, "error": str(e)},
            )
            response = MessageResponse(
                text=(
                    "❌ שגיאה בשמירת הסלפי. נסה שוב.\n\n"
                    "📸 שלח תמונת סלפי:"
                ),
                keyboard=[["🔄 נסה שוב"], ["❌ ביטול"]],
            )
            return response, DriverState.VERIFY_COLLECT_SELFIE.value, {}

        response = MessageResponse(
            text=(
                "✅ סלפי התקבל!\n\n"
                "🪪 <b>שלב אימות 2 מתוך 2</b>\n"
                "שלח תמונה של תעודת הזהות שלך.\n"
                "ודא שכל הפרטים קריאים."
            ),
            keyboard=[["🪪 צלם תעודה"], ["❌ ביטול"]],
        )
        return response, DriverState.VERIFY_COLLECT_ID_DOCUMENT.value, {}

    async def _handle_verify_collect_id_document(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        איסוף תמונת תעודת זהות.

        כשמתקבלת תמונה — שומר, מעדכן סטטוס ל-PENDING, שולח לאדמין.
        """
        photo_file_id: str | None = kwargs.get("photo_file_id")

        # ביטול
        if message and "ביטול" in message.strip():
            response = MessageResponse(
                text=(
                    "❌ האימות בוטל.\n"
                    "תוכל לחזור לאימות בכל שלב דרך התפריט."
                ),
            )
            return response, DriverState.MENU.value, {}

        if not photo_file_id:
            response = MessageResponse(
                text=(
                    "🪪 <b>שלב אימות 2 מתוך 2</b>\n"
                    "שלח תמונה של תעודת הזהות שלך.\n"
                    "ודא שכל הפרטים קריאים."
                ),
                keyboard=[["🪪 צלם תעודה"], ["❌ ביטול"]],
            )
            return response, DriverState.VERIFY_COLLECT_ID_DOCUMENT.value, {}

        # תמונה התקבלה — שמירה ועדכון סטטוס
        try:
            await self.verification_service.submit_id_document(user.id, photo_file_id)
        except (ValidationException, NotFoundException) as e:
            logger.error(
                "כשלון בשמירת תעודה",
                extra_data={"user_id": user.id, "error": str(e)},
            )
            response = MessageResponse(
                text=(
                    "❌ שגיאה בשמירת התעודה. נסה שוב.\n\n"
                    "🪪 שלח תמונה של תעודת הזהות:"
                ),
                keyboard=[["🔄 נסה שוב"], ["❌ ביטול"]],
            )
            return response, DriverState.VERIFY_COLLECT_ID_DOCUMENT.value, {}

        # שליחת כרטיס נהג + תמונות לאדמין
        await self._notify_admin_driver_verification(user)

        response = MessageResponse(
            text=(
                "✅ <b>תעודת הזהות התקבלה!</b>\n\n"
                "⏳ הבקשה שלך נשלחה לבדיקה.\n"
                "נעדכן אותך ברגע שמנהל יבדוק את הפרטים.\n\n"
                "⏱ בדרך כלל האימות מתבצע תוך מספר שעות."
            ),
        )
        return response, DriverState.VERIFY_PENDING_APPROVAL.value, {}

    async def _handle_verify_pending_approval(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """ממתין לאישור מנהל — הודעת סטטוס"""
        response = MessageResponse(
            text=(
                "⏳ <b>הבקשה שלך בבדיקה</b>\n\n"
                "האימות שלך ממתין לאישור מנהל.\n"
                "נעדכן אותך ברגע שתהיה החלטה.\n\n"
                "⏱ בדרך כלל האימות מתבצע תוך מספר שעות."
            ),
        )
        return response, DriverState.VERIFY_PENDING_APPROVAL.value, {}

    async def _notify_admin_driver_verification(self, user: User) -> None:
        """שליחת כרטיס אימות נהג למנהלים"""
        try:
            from app.domain.services.admin_notification_service import (
                AdminNotificationService,
            )

            result = await self.db.execute(
                select(DriverProfile).where(DriverProfile.user_id == user.id)
            )
            profile = result.scalar_one_or_none()
            if not profile:
                logger.error(
                    "פרופיל נהג לא נמצא בעת שליחת התראה לאדמין",
                    extra_data={"user_id": user.id},
                )
                return

            await AdminNotificationService.notify_new_driver_verification(
                user_id=user.id,
                full_name=user.full_name or user.name or "לא צוין",
                dress_code=profile.dress_code,
                vehicle_description=profile.vehicle_description or "לא צוין",
                platform=self.platform,
                phone_or_chat_id=(
                    str(user.telegram_chat_id)
                    if self.platform == "telegram" and user.telegram_chat_id
                    else user.phone_number or "לא צוין"
                ),
                selfie_file_id=profile.verification_selfie_file_id,
                id_file_id=profile.verification_id_file_id,
            )
        except Exception as e:
            # כשלון בהתראה לא עוצר את הזרימה — הנהג יקבל הודעת "בבדיקה"
            logger.error(
                "כשלון בשליחת התראת אימות נהג לאדמין",
                extra_data={"user_id": user.id, "error": str(e)},
                exc_info=True,
            )

    # ==================== סשן 4: תפריט ראשי ====================

    async def _build_main_menu(
        self, user: User
    ) -> Tuple[MessageResponse, str, dict]:
        """בניית תגובת תפריט ראשי — משותף ל-INITIAL (נהג רשום) ול-MENU"""
        try:
            text, keyboard = await self.menu_service.get_main_menu(user.id)
        except NotFoundException:
            logger.error(
                "פרופיל נהג לא נמצא בבניית תפריט",
                extra_data={"user_id": user.id},
            )
            response = MessageResponse(
                text="❌ שגיאה בטעינת התפריט. נסה שוב מאוחר יותר.",
            )
            return response, DriverState.MENU.value, {}

        response = MessageResponse(text=text, keyboard=keyboard)
        return response, DriverState.MENU.value, {}

    async def _handle_menu(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        תפריט ראשי — ניתוב לפי בחירת כפתור.

        כפתורים:
        - 🛠 הגדרות חיפוש → SETTINGS_VIEW
        - 📖 הוראות שימוש → הודעת עזרה (נשאר ב-MENU)
        - ת / תפריט → רענון התפריט
        """
        msg = message.strip()

        # ניתוב להגדרות חיפוש
        if "הגדרות" in msg:
            return await self._build_settings_menu(user)

        # הוראות שימוש
        if "הוראות" in msg or "עזרה" in msg:
            response = MessageResponse(
                text=(
                    "📖 <b>הוראות שימוש</b>\n\n"
                    "🔹 <b>תפריט</b> — שלח 'ת' או 'תפריט' לפתיחת התפריט הראשי\n"
                    "🔹 <b>הגדרות</b> — שנה סוג רכב, סוג נסיעה, מסגרת זמן ועוד\n"
                    "🔹 <b>חיפוש</b> — שלח 'פ' ואחריו יעד לחיפוש נסיעות\n\n"
                    "📋 לחזרה לתפריט שלח 'ת'"
                ),
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        # ברירת מחדל — רענון תפריט ראשי
        return await self._build_main_menu(user)

    # ==================== סשן 4: הגדרות חיפוש ====================

    async def _build_settings_menu(
        self, user: User
    ) -> Tuple[MessageResponse, str, dict]:
        """בניית תגובת תפריט הגדרות — משותף ל-MENU ול-SETTINGS_VIEW"""
        text, keyboard = await self.menu_service.get_settings_menu(user.id)
        response = MessageResponse(text=text, keyboard=keyboard)
        return response, DriverState.SETTINGS_VIEW.value, {}

    async def _handle_settings_view(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        תפריט הגדרות — ניתוב לפי בחירת כפתור.
        """
        msg = message.strip()

        # חזרה להגדרות (כפתור "🔙 חזרה להגדרות" אחרי עדכון הגדרה)
        if "להגדרות" in msg:
            return await self._build_settings_menu(user)

        # חזרה לתפריט ראשי (כפתור "🔙 חזרה לתפריט")
        if "חזרה" in msg or "תפריט" in msg:
            return await self._build_main_menu(user)

        # ניתוב לפי בחירה
        if "סוג רכב" in msg or "🚙" in msg:
            return self._show_vehicle_type_options()

        if "סוג נסיעה" in msg or "🛣" in msg:
            return self._show_trip_type_options()

        if "משלוחים" in msg or "💌" in msg:
            return self._show_deliveries_options()

        if "מסגרת זמן" in msg or "🕐" in msg:
            return self._show_timeframe_options()

        if "עתידי" in msg or "📅" in msg:
            return self._show_future_only_options()

        # בחירה לא מוכרת — הצגת הגדרות מחדש
        return await self._build_settings_menu(user)

    # ---------- סוג רכב ----------

    def _show_vehicle_type_options(self) -> Tuple[MessageResponse, str, dict]:
        """הצגת אפשרויות סוג רכב"""
        keyboard = [[label] for label in VEHICLE_TYPE_LABELS.values()]
        keyboard.append(["❌ ביטול"])
        response = MessageResponse(
            text="🚙 <b>בחר סוג רכב:</b>",
            keyboard=keyboard,
        )
        return response, DriverState.SETTINGS_VEHICLE_TYPE.value, {}

    async def _handle_settings_vehicle_type(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """טיפול בבחירת סוג רכב"""
        msg = message.strip()

        # ביטול
        if "ביטול" in msg:
            return await self._build_settings_menu(user)

        # מיפוי מטקסט כפתור לערך enum
        vehicle_value = VEHICLE_TYPE_BY_LABEL.get(msg)
        if not vehicle_value:
            # ניסיון ערך enum ישיר
            valid_values = {e.value for e in VehicleCategory}
            if msg in valid_values:
                vehicle_value = msg

        if not vehicle_value:
            keyboard = [[label] for label in VEHICLE_TYPE_LABELS.values()]
            keyboard.append(["❌ ביטול"])
            response = MessageResponse(
                text="❌ בחירה לא תקינה. אנא בחר מהרשימה:",
                keyboard=keyboard,
            )
            return response, DriverState.SETTINGS_VEHICLE_TYPE.value, {}

        try:
            label = await self.menu_service.update_vehicle_type(user.id, vehicle_value)
        except (ValidationException, ValueError) as e:
            err_msg = e.message if hasattr(e, "message") else str(e)
            response = MessageResponse(text=f"❌ {err_msg}")
            return response, DriverState.SETTINGS_VEHICLE_TYPE.value, {}

        response = MessageResponse(
            text=f"✅ סוג רכב עודכן ל: {escape(label)}",
            keyboard=[["🔙 חזרה להגדרות"], ["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.SETTINGS_VIEW.value, {}

    # ---------- סוג נסיעה ----------

    def _show_trip_type_options(self) -> Tuple[MessageResponse, str, dict]:
        """הצגת אפשרויות סוג נסיעה"""
        keyboard = [[label] for label in TRIP_TYPE_LABELS.values()]
        keyboard.append(["❌ ביטול"])
        response = MessageResponse(
            text="🛣 <b>בחר סוג נסיעה:</b>",
            keyboard=keyboard,
        )
        return response, DriverState.SETTINGS_TRIP_TYPE.value, {}

    async def _handle_settings_trip_type(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """טיפול בבחירת סוג נסיעה"""
        msg = message.strip()

        if "ביטול" in msg:
            return await self._build_settings_menu(user)

        trip_value = TRIP_TYPE_BY_LABEL.get(msg)
        if not trip_value:
            valid_values = {e.value for e in TripTypeFilter}
            if msg in valid_values:
                trip_value = msg

        if not trip_value:
            keyboard = [[label] for label in TRIP_TYPE_LABELS.values()]
            keyboard.append(["❌ ביטול"])
            response = MessageResponse(
                text="❌ בחירה לא תקינה. אנא בחר מהרשימה:",
                keyboard=keyboard,
            )
            return response, DriverState.SETTINGS_TRIP_TYPE.value, {}

        try:
            label = await self.menu_service.update_trip_type(user.id, trip_value)
        except (ValidationException, ValueError) as e:
            err_msg = e.message if hasattr(e, "message") else str(e)
            response = MessageResponse(text=f"❌ {err_msg}")
            return response, DriverState.SETTINGS_TRIP_TYPE.value, {}

        response = MessageResponse(
            text=f"✅ סוג נסיעה עודכן ל: {escape(label)}",
            keyboard=[["🔙 חזרה להגדרות"], ["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.SETTINGS_VIEW.value, {}

    # ---------- הצגת משלוחים ----------

    def _show_deliveries_options(self) -> Tuple[MessageResponse, str, dict]:
        """הצגת אפשרויות הצגת משלוחים"""
        response = MessageResponse(
            text=(
                "💌 <b>הצגת משלוחים בתוצאות חיפוש</b>\n\n"
                "האם להציג משלוחים בנוסף לנסיעות?"
            ),
            keyboard=[["✅ כן — הצג משלוחים"], ["❌ לא — ללא משלוחים"], ["❌ ביטול"]],
        )
        return response, DriverState.SETTINGS_SHOW_DELIVERIES.value, {}

    async def _handle_settings_show_deliveries(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """טיפול בבחירת הצגת משלוחים"""
        msg = message.strip()

        if "ביטול" in msg and "ללא" not in msg:
            return await self._build_settings_menu(user)

        show: bool | None = None
        if "כן" in msg:
            show = True
        elif "לא" in msg or "ללא" in msg:
            show = False

        if show is None:
            response = MessageResponse(
                text="❌ בחירה לא תקינה. אנא בחר כן או לא:",
                keyboard=[["✅ כן — הצג משלוחים"], ["❌ לא — ללא משלוחים"], ["❌ ביטול"]],
            )
            return response, DriverState.SETTINGS_SHOW_DELIVERIES.value, {}

        await self.menu_service.update_show_deliveries(user.id, show)

        label = "כן ✅" if show else "לא ❌"
        response = MessageResponse(
            text=f"✅ הצגת משלוחים עודכנה ל: {label}",
            keyboard=[["🔙 חזרה להגדרות"], ["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.SETTINGS_VIEW.value, {}

    # ---------- מסגרת זמן ----------

    def _show_timeframe_options(self) -> Tuple[MessageResponse, str, dict]:
        """הצגת אפשרויות מסגרת זמן"""
        keyboard = [[label] for label in TIMEFRAME_LABELS.values()]
        keyboard.append(["❌ ביטול"])
        response = MessageResponse(
            text="🕐 <b>בחר מסגרת זמן לנסיעות קרובות:</b>",
            keyboard=keyboard,
        )
        return response, DriverState.SETTINGS_UPCOMING_TIMEFRAME.value, {}

    async def _handle_settings_timeframe(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """טיפול בבחירת מסגרת זמן"""
        msg = message.strip()

        if "ביטול" in msg:
            return await self._build_settings_menu(user)

        timeframe_value = TIMEFRAME_BY_LABEL.get(msg)
        if not timeframe_value:
            valid_values = {e.value for e in UpcomingTimeframe}
            if msg in valid_values:
                timeframe_value = msg

        if not timeframe_value:
            keyboard = [[label] for label in TIMEFRAME_LABELS.values()]
            keyboard.append(["❌ ביטול"])
            response = MessageResponse(
                text="❌ בחירה לא תקינה. אנא בחר מהרשימה:",
                keyboard=keyboard,
            )
            return response, DriverState.SETTINGS_UPCOMING_TIMEFRAME.value, {}

        try:
            label = await self.menu_service.update_timeframe(user.id, timeframe_value)
        except (ValidationException, ValueError) as e:
            err_msg = e.message if hasattr(e, "message") else str(e)
            response = MessageResponse(text=f"❌ {err_msg}")
            return response, DriverState.SETTINGS_UPCOMING_TIMEFRAME.value, {}

        response = MessageResponse(
            text=f"✅ מסגרת זמן עודכנה ל: {escape(label)}",
            keyboard=[["🔙 חזרה להגדרות"], ["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.SETTINGS_VIEW.value, {}

    # ---------- חיפוש עתידי ----------

    def _show_future_only_options(self) -> Tuple[MessageResponse, str, dict]:
        """הצגת אפשרויות חיפוש עתידי"""
        response = MessageResponse(
            text=(
                "📅 <b>חיפוש עתידי בלבד</b>\n\n"
                "⚠️ שים לב: כרגע אין אפשרות לחפש גם נסיעה מיידית "
                "וגם עתידית בו-זמנית.\n\n"
                "אם תפעיל חיפוש עתידי, תראה רק נסיעות "
                "שמתחילות אחרי השעה שתציין.\n\n"
                "האם להפעיל חיפוש עתידי?"
            ),
            keyboard=[["✅ כן — הפעל חיפוש עתידי"], ["❌ לא — כבה חיפוש עתידי"], ["❌ ביטול"]],
        )
        return response, DriverState.SETTINGS_FUTURE_ONLY_MODE.value, {}

    async def _handle_settings_future_only(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """טיפול בהפעלה/כיבוי חיפוש עתידי"""
        msg = message.strip()

        if "ביטול" in msg and "כבה" not in msg:
            return await self._build_settings_menu(user)

        if "כן" in msg or "הפעל" in msg:
            # מעבר לשלב הזנת שעה
            response = MessageResponse(
                text=(
                    "🕐 <b>הזן שעת התחלה לחיפוש עתידי</b>\n\n"
                    "הזן שעה בפורמט HH:MM\n"
                    "לדוגמה: 08:00 או 14:30"
                ),
                keyboard=[["❌ ביטול"]],
            )
            return response, DriverState.SETTINGS_START_TIME.value, {}

        if "לא" in msg or "כבה" in msg:
            try:
                await self.menu_service.update_future_only(user.id, False, None)
            except (ValidationException, ValueError) as e:
                err_msg = e.message if hasattr(e, "message") else str(e)
                response = MessageResponse(text=f"❌ {err_msg}")
                return response, DriverState.SETTINGS_FUTURE_ONLY_MODE.value, {}

            response = MessageResponse(
                text="✅ חיפוש עתידי כובה",
                keyboard=[["🔙 חזרה להגדרות"], ["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.SETTINGS_VIEW.value, {}

        # בחירה לא מוכרת
        response = MessageResponse(
            text="❌ בחירה לא תקינה. בחר כן או לא:",
            keyboard=[["✅ כן — הפעל חיפוש עתידי"], ["❌ לא — כבה חיפוש עתידי"], ["❌ ביטול"]],
        )
        return response, DriverState.SETTINGS_FUTURE_ONLY_MODE.value, {}

    async def _handle_settings_start_time(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """טיפול בהזנת שעת התחלה לחיפוש עתידי"""
        msg = message.strip()

        if "ביטול" in msg:
            return await self._build_settings_menu(user)

        # פרסור שעה בפורמט HH:MM
        parsed_time = self._parse_time(msg)
        if parsed_time is None:
            response = MessageResponse(
                text=(
                    "❌ פורמט שעה לא תקין.\n\n"
                    "הזן שעה בפורמט HH:MM\n"
                    "לדוגמה: 08:00 או 14:30"
                ),
                keyboard=[["❌ ביטול"]],
            )
            return response, DriverState.SETTINGS_START_TIME.value, {}

        try:
            await self.menu_service.update_future_only(user.id, True, parsed_time)
        except (ValidationException, ValueError) as e:
            err_msg = e.message if hasattr(e, "message") else str(e)
            response = MessageResponse(
                text=f"❌ {err_msg}",
                keyboard=[["❌ ביטול"]],
            )
            return response, DriverState.SETTINGS_START_TIME.value, {}

        response = MessageResponse(
            text=f"✅ חיפוש עתידי הופעל — משעה {parsed_time.strftime('%H:%M')}",
            keyboard=[["🔙 חזרה להגדרות"], ["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.SETTINGS_VIEW.value, {}

    @staticmethod
    def _parse_time(time_str: str) -> time_type | None:
        """פרסור שעה בפורמט HH:MM"""
        import re
        match = re.match(r"^(\d{1,2}):(\d{2})$", time_str.strip())
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return time_type(hour, minute)

    # ==================== fallback ====================

    async def _handle_unknown(
        self, user: User, message: str, context: dict, **kwargs: object
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
