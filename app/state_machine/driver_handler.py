"""
Driver State Handler — זרימת רישום ואימות נהג (iDriver סשנים 2-3)

מנהל את מכונת המצבים של רישום נהג חדש:
1. REGISTER_COLLECT_NAME — שם מלא
2. REGISTER_COLLECT_BIRTH_DATE — תאריך לידה
3. REGISTER_COLLECT_VEHICLE — תיאור רכב
4. REGISTER_COLLECT_DRESS_CODE — קוד לבוש (עם הסתעפות לאימות/תפריט)

ואימות חרדי (סשן 3):
5. VERIFY_COLLECT_SELFIE — תמונת סלפי
6. VERIFY_COLLECT_ID_DOCUMENT — תעודת זהות
7. VERIFY_PENDING_APPROVAL — המתנה לאישור מנהל
"""

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
    DriverVerificationStatus,
)
from app.domain.services.driver_registration_service import DriverRegistrationService
from app.domain.services.driver_verification_service import DriverVerificationService
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
    Handler לזרימת רישום ואימות נהג.

    מטפל בשלבי הרישום (סשן 2), אימות חרדי (סשן 3) וב-INITIAL/NEW.
    שלבים נוספים (תפריט, הגדרות, חיפוש) יתווספו בסשנים הבאים.
    """

    def __init__(self, db: AsyncSession, platform: str = "telegram"):
        self.db = db
        self.platform = platform
        self.state_manager = StateManager(db)
        self.registration_service = DriverRegistrationService(db)
        self.verification_service = DriverVerificationService(db)

    def _is_registration_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת רישום או אימות (לניקוי קונטקסט)"""
        return state.startswith(("DRIVER.REGISTER.", "DRIVER.VERIFY."))

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
            # תפריט ראשי — placeholder עד סשנים הבאים
            DriverState.MENU.value: self._handle_post_registration_placeholder,
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
            response = MessageResponse(
                text="🚗 המערכת בהקמה, נעדכן אותך בקרוב.",
            )
            return response, DriverState.MENU.value, {}

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

    # ==================== placeholder למצבים עתידיים ====================

    async def _handle_post_registration_placeholder(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        Placeholder למצבים שהרישום מוביל אליהם (MENU).
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
