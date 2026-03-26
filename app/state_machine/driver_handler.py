"""
Driver State Handler — זרימת רישום, אימות, תפריט, הגדרות וחיפוש נהג (iDriver סשנים 2-7)

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

חיפוש נסיעות (סשן 5):
16. SEARCH_VIEW_ACTIVE — צפייה בחיפושים פעילים
17. SEARCH_MANAGE — ניהול (מחיקת) חיפוש בודד
18. SEARCH_CREATE_ORIGIN — המתנה לשיתוף מיקום GPS

ניהול חיפושים + סשן 24 שעות (סשן 6):
19. פקודת "ע" — השהיית כל החיפושים
20. פקודת "ה" — חידוש חיפושים מושהים
21. פקודת "מ" — מחיקת חיפוש בודד (רשימה לבחירה)
22. פקודת "ממ" — מחיקת כל החיפושים (עם אישור)

פרסום נסיעות + מחירון (סשן 7):
23. פרסום נסיעה חופשית — "בב ים 5 מק 150 ש״ח"
24. פקודת "מחירון" — "מחירון בב ים"
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
    DriverSubscriptionStatus,
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
from app.domain.services.driver_search_service import DriverSearchService
from app.domain.services.city_abbreviation_service import CityAbbreviationService
from app.domain.services.ride_posting_service import ParsedRidePosting, RidePostingService
from app.domain.services.pricing_service import PricingService
from app.domain.services.driver_subscription_service import DriverSubscriptionService
from app.db.models.driver_search import MAX_ACTIVE_SEARCHES_PER_USER, DriverSearchStatus
from sqlalchemy import select
from app.domain.services.station_service import StationService
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

# מפתחות קונטקסט של זרימת חיפוש — ניקוי בחזרה לתפריט/צפייה
_SEARCH_CONTEXT_KEYS = {
    "search_ids",
    "delete_all_pending",
}


class DriverStateHandler:
    """
    Handler לזרימת רישום, אימות, תפריט, הגדרות וחיפוש נהג.

    מטפל בשלבי הרישום (סשן 2), אימות חרדי (סשן 3),
    תפריט ראשי והגדרות חיפוש (סשן 4), חיפוש נסיעות (סשן 5),
    ניהול חיפושים + סשן 24 שעות (סשן 6),
    ופרסום נסיעות + מחירון (סשן 7).
    """

    def __init__(self, db: AsyncSession, platform: str = "telegram"):
        self.db = db
        self.platform = platform
        self.state_manager = StateManager(db)
        self.registration_service = DriverRegistrationService(db)
        self.verification_service = DriverVerificationService(db)
        self.menu_service = DriverMenuService(db)
        self.search_service = DriverSearchService(db)
        # סשן 6 — ניהול סשנים
        from app.domain.services.driver_session_service import DriverSessionService
        self.session_service = DriverSessionService(db)
        # סשן 7 — פרסום נסיעות + מחירון
        self.ride_posting_service = RidePostingService(db)
        # סשן 8 — מנויים
        self.subscription_service = DriverSubscriptionService(db)

    def _is_registration_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת רישום או אימות (לניקוי קונטקסט)"""
        return state.startswith(("DRIVER.REGISTER.", "DRIVER.VERIFY."))

    def _is_settings_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת הגדרות (לניקוי קונטקסט)"""
        return state.startswith("DRIVER.SETTINGS.")

    async def handle_message(
        self,
        user: User,
        message: str,
        photo_file_id: str | None = None,
        location_lat: float | None = None,
        location_lng: float | None = None,
    ) -> Tuple[MessageResponse, str]:
        """
        עיבוד הודעה נכנסת מנהג.

        Args:
            user: אובייקט המשתמש
            message: טקסט ההודעה
            photo_file_id: מזהה תמונה (בשימוש בשלבי אימות — סשן 3)
            location_lat: קו רוחב (חיפוש לפי מיקום — סשן 5)
            location_lng: קו אורך (חיפוש לפי מיקום — סשן 5)

        Returns:
            tuple של (תגובה, מצב חדש)
        """
        platform = self.platform
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(
            user, message, context,
            photo_file_id=photo_file_id,
            location_lat=location_lat,
            location_lng=location_lng,
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

        # ניקוי קונטקסט חיפוש ביציאה מ-SEARCH_MANAGE
        if (
            current_state == DriverState.SEARCH_MANAGE.value
            and new_state != DriverState.SEARCH_MANAGE.value
        ):
            stale_keys = _SEARCH_CONTEXT_KEYS & context.keys()
            if stale_keys:
                clean_ctx = {
                    k: v for k, v in context.items() if k not in _SEARCH_CONTEXT_KEYS
                }
                if context_update:
                    clean_ctx.update(context_update)
                await self.state_manager.force_state(
                    user.id, platform, new_state, clean_ctx
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
            # חיפוש נסיעות (סשן 5)
            DriverState.SEARCH_VIEW_ACTIVE.value: self._handle_search_view_active,
            DriverState.SEARCH_MANAGE.value: self._handle_search_manage,
            DriverState.SEARCH_CREATE_ORIGIN.value: self._handle_search_create_location,
            # אישור פרסום נסיעה
            DriverState.RIDE_POSTING_CONFIRM.value: self._handle_ride_posting_confirm,
            # מנוי (סשן 8)
            DriverState.SUBSCRIPTION_VIEW.value: self._handle_subscription_view,
            DriverState.SUBSCRIPTION_PURCHASE.value: self._handle_subscription_purchase,
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
            button_text="👔 בחירת סוג לבוש",
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
                text="❌ לא זיהיתי. 👔 בחירת סוג לבוש:",
                keyboard=_DRESS_CODE_KEYBOARD,
                button_text="👔 בחירת סוג לבוש",
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
                telegram_username=user.telegram_username,
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

        response = MessageResponse(text=text, keyboard=keyboard, button_text="🚗 תפריט ראשי")
        return response, DriverState.MENU.value, {}

    async def _handle_menu(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        תפריט ראשי — ניתוב לפי בחירת כפתור או פקודת חיפוש.

        כפתורים:
        - 🛠 הגדרות חיפוש → SETTINGS_VIEW
        - 🔍 חיפושים פעילים → SEARCH_VIEW_ACTIVE
        - 📖 הוראות שימוש → הודעת עזרה (נשאר ב-MENU)
        - ת / תפריט → רענון התפריט

        פקודות טקסט:
        - "פ <יעד>" → חיפוש ליעד
        - "פ <מוצא> <יעד>" → חיפוש ממוצא ליעד
        - "פ א <יעד>" → חיפוש אזורי
        - "פ מיקום" → חיפוש לפי מיקום GPS
        - "<מוצא> <יעד> <מקומות> מק <מחיר> ש״ח" → פרסום נסיעה (סשן 7)
        - "מחירון <מוצא> <יעד>" → מחירון (סשן 7)
        - "מילון" → רשימת קיצורי ערים
        """
        msg = message.strip()

        # פקודת חיפוש — "פ ..."
        if CityAbbreviationService.is_search_command(msg):
            return await self._handle_search_command(user, msg)

        # סשן 7: פקודת מחירון — "מחירון ..." (לפני פקודות חד-אותיות)
        if PricingService.is_pricing_command(msg):
            return self._handle_pricing_command(msg)

        # סשן 7: פרסום נסיעה — "<מוצא> <יעד> <מקומות> מק <מחיר>"
        if RidePostingService.is_ride_posting(msg):
            return await self._handle_ride_posting(user, msg)

        # סשן 6: פקודת השהיית חיפושים — "ע"
        if msg == "ע":
            return await self._handle_pause_searches(user)

        # סשן 6: פקודת חידוש חיפושים — "ה"
        if msg == "ה":
            return await self._handle_resume_searches(user)

        # סשן 6: פקודת מחיקת כל החיפושים — "ממ" (לפני "מ" כדי לא לתפוס "ממ" כ-"מ")
        if msg == "ממ":
            return self._show_delete_all_confirmation()

        # סשן 6: פקודת מחיקת חיפוש בודד — "מ"
        if msg == "מ":
            return await self._build_search_delete_menu(user)

        # סשן 8: ניתוב למנוי
        if "מנוי" in msg or "💳" in msg:
            return await self._build_subscription_view(user)

        # ניתוב להגדרות חיפוש
        if "הגדרות" in msg:
            return await self._build_settings_menu(user)

        # צפייה בחיפושים פעילים
        if "חיפושים" in msg or "🔍" in msg:
            return await self._build_search_view(user)

        # מילון קיצורי ערים
        if msg == "מילון":
            return self._show_abbreviations_help()

        # הוראות שימוש
        if "הוראות" in msg or "עזרה" in msg or "מדריך" in msg:
            return self._show_help()

        # ברירת מחדל — רענון תפריט ראשי
        return await self._build_main_menu(user)

    def _show_help(self) -> Tuple[MessageResponse, str, dict]:
        """הצגת הוראות שימוש"""
        response = MessageResponse(
            text=(
                "📖 <b>הוראות שימוש</b>\n\n"
                "🔹 <b>תפריט</b> — שלח 'ת' או 'תפריט' לפתיחת התפריט הראשי\n"
                "🔹 <b>הגדרות</b> — שנה סוג רכב, סוג נסיעה, מסגרת זמן ועוד\n\n"
                "🔍 <b>חיפוש נסיעות</b>\n"
                "🔹 <b>פ ים</b> — חיפוש נסיעות לירושלים\n"
                "🔹 <b>פ בב ים</b> — חיפוש מבני ברק לירושלים\n"
                "🔹 <b>פ א טב</b> — חיפוש אזורי לטבריה\n"
                "🔹 <b>פ מיקום</b> — חיפוש לפי שיתוף מיקום GPS\n\n"
                "📋 <b>ניהול חיפושים</b>\n"
                "🔹 <b>חיפושים</b> — צפייה בחיפושים פעילים\n"
                "🔹 <b>ע</b> — השהיית כל החיפושים\n"
                "🔹 <b>ה</b> — חידוש חיפושים מושהים\n"
                "🔹 <b>מ</b> — מחיקת חיפוש בודד\n"
                "🔹 <b>ממ</b> — מחיקת כל החיפושים\n"
                "🔹 <b>מילון</b> — רשימת קיצורי ערים\n\n"
                "🚗 <b>פרסום נסיעה</b>\n"
                "🔹 <b>בב ים 5 מק 150 ש״ח</b> — פרסום נסיעה חופשית\n"
                "   (מוצא, יעד, מקומות, מחיר)\n\n"
                "💰 <b>מחירון</b>\n"
                "🔹 <b>מחירון בב ים</b> — מחיר מומלץ למסלול\n\n"
                "📋 לחזרה לתפריט שלח 'ת'"
            ),
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.MENU.value, {}

    def _show_abbreviations_help(self) -> Tuple[MessageResponse, str, dict]:
        """הצגת מילון קיצורי ערים"""
        abbreviations = CityAbbreviationService.get_abbreviations_help()
        response = MessageResponse(
            text=(
                "📚 <b>מילון קיצורי ערים</b>\n\n"
                f"<pre>{abbreviations}</pre>\n\n"
                "💡 אפשר גם לכתוב את שם העיר המלא.\n"
                "📋 לחזרה לתפריט שלח 'ת'"
            ),
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.MENU.value, {}

    # ==================== סשן 7: פרסום נסיעות + מחירון ====================

    async def _handle_ride_posting(
        self, user: User, text: str
    ) -> Tuple[MessageResponse, str, dict]:
        """
        טיפול בפרסום נסיעה חופשית.

        פורמט: "<מוצא> <יעד> <מקומות> מק <מחיר> ש״ח"
        למשל: "בב ים 5 מק 150 ש״ח"
        """
        posting = RidePostingService.parse_ride_posting(text)
        if not posting:
            response = MessageResponse(
                text=(
                    "❌ <b>פורמט פרסום לא תקין</b>\n\n"
                    "פורמט נכון:\n"
                    "<code>בב ים 5 מק 150 ש״ח</code>\n\n"
                    "📍 מוצא + יעד (קיצור או שם מלא)\n"
                    "👥 מספר מקומות + מק\n"
                    "💰 מחיר בש\"ח\n\n"
                    "📋 לחזרה לתפריט שלח 'ת'"
                ),
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        # בדיקת מנוי פעיל — רק אחרי פרסור מוצלח של הפקודה,
        # כדי לחסוך שאילתת DB מיותרת על פקודות לא תקינות
        if not await self.subscription_service.is_subscription_active(user.id):
            response = MessageResponse(
                text=(
                    "⚠️ <b>המנוי שלך פג תוקף</b>\n\n"
                    "רכוש מנוי כדי להמשיך לפרסם נסיעות.\n"
                    "לחץ על כפתור 'מנוי' לפרטים."
                ),
                keyboard=[["💳 מנוי"], ["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        # ספירת נהגים עם חיפושים תואמים ליעד הנסיעה
        matching_ids = await self.search_service.get_matching_driver_user_ids(
            origin_city=posting.origin,
            destination_city=posting.destination,
            exclude_user_id=user.id,
        )

        driver_name = user.full_name or user.name or "נהג"
        message = RidePostingService.format_ride_message(posting, driver_name)

        preview = (
            f"📋 <b>תצוגה מקדימה של הנסיעה:</b>\n\n"
            f"{message}\n"
            f"🎲 {len(matching_ids)} נהגים עם חיפוש תואם יקבלו התראה\n\n"
            f"לחץ <b>✅ אישור פרסום</b> לפרסום הנסיעה,\n"
            f"או <b>❌ ביטול</b> לחזרה לתפריט."
        )

        response = MessageResponse(
            text=preview,
            keyboard=[["✅ אישור פרסום"], ["❌ ביטול"]],
        )
        return response, DriverState.RIDE_POSTING_CONFIRM.value, {
            "ride_origin": posting.origin,
            "ride_destination": posting.destination,
            "ride_seats": str(posting.seats),
            "ride_price": str(posting.price),
        }

    async def _handle_ride_posting_confirm(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        אישור או ביטול פרסום נסיעה.
        """
        msg = message.strip()

        # ניקוי context של הנסיעה
        _ride_context_cleanup = {
            "ride_origin": None,
            "ride_destination": None,
            "ride_seats": None,
            "ride_price": None,
        }

        # ביטול
        if "ביטול" in msg or "חזרה" in msg:
            response = MessageResponse(
                text="❌ הפרסום בוטל.",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, _ride_context_cleanup

        # אישור פרסום
        if "אישור" in msg:
            origin = context.get("ride_origin", "")
            destination = context.get("ride_destination", "")
            seats_str = context.get("ride_seats", "0")
            price_str = context.get("ride_price", "0")

            try:
                seats = int(seats_str)
                price = float(price_str)
            except (ValueError, TypeError):
                response = MessageResponse(
                    text="❌ שגיאה בנתוני הנסיעה. נסה שוב.",
                    keyboard=[["🔙 חזרה לתפריט"]],
                )
                return response, DriverState.MENU.value, _ride_context_cleanup

            posting = ParsedRidePosting(
                origin=origin,
                destination=destination,
                seats=seats,
                price=price,
            )

            success, formatted_message, sent_count, total_groups = (
                await self.ride_posting_service.post_ride(user, posting)
            )

            # שליחת הודעות פרטיות לנהגים עם חיפושים תואמים — תמיד, גם כשאין קבוצות
            matching_ids = await self.search_service.get_matching_driver_user_ids(
                origin_city=posting.origin,
                destination_city=posting.destination,
                exclude_user_id=user.id,
            )
            driver_name = user.full_name or user.name or "נהג"
            notified_count = await self.ride_posting_service.notify_matching_drivers(
                posting, driver_name, matching_ids
            )

            if sent_count > 0:
                notified_text = ""
                if notified_count > 0:
                    notified_text = f"📨 נשלח ל-{notified_count} נהגים עם חיפוש תואם.\n"
                confirmation = (
                    f"✅ <b>הנסיעה פורסמה בהצלחה!</b>\n\n"
                    f"{formatted_message}\n"
                    f"📢 פורסם ב-{sent_count} קבוצות.\n"
                    f"{notified_text}"
                )
            elif total_groups == 0:
                notified_text = ""
                if notified_count > 0:
                    notified_text = f"📨 נשלח ל-{notified_count} נהגים עם חיפוש תואם.\n"
                confirmation = (
                    f"⚠️ <b>לא נמצאו קבוצות רלוונטיות</b>\n\n"
                    f"{formatted_message}\n"
                    f"לא נמצאו קבוצות מתאימות למסלול "
                    f"{escape(posting.origin)} → {escape(posting.destination)}.\n"
                    f"{notified_text}"
                )
            else:
                # נמצאו קבוצות אבל כל השליחות נכשלו
                notified_text = ""
                if notified_count > 0:
                    notified_text = f"📨 עם זאת, נשלח ל-{notified_count} נהגים עם חיפוש תואם.\n"
                confirmation = (
                    f"❌ <b>שגיאה בפרסום הנסיעה</b>\n\n"
                    f"{formatted_message}\n"
                    f"נמצאו {total_groups} קבוצות רלוונטיות אך השליחה נכשלה.\n"
                    f"{notified_text}"
                    f"נסה שוב מאוחר יותר."
                )

            response = MessageResponse(
                text=confirmation,
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, _ride_context_cleanup

        # לא זוהתה פקודה — חזרה לאישור
        response = MessageResponse(
            text="לחץ <b>✅ אישור פרסום</b> לפרסום, או <b>❌ ביטול</b> לחזרה.",
            keyboard=[["✅ אישור פרסום"], ["❌ ביטול"]],
        )
        return response, DriverState.RIDE_POSTING_CONFIRM.value, None

    def _handle_pricing_command(
        self, text: str
    ) -> Tuple[MessageResponse, str, dict]:
        """
        טיפול בפקודת מחירון.

        פורמט: "מחירון <מוצא> <יעד>"
        למשל: "מחירון בב ים"
        """
        parsed = PricingService.parse_pricing_command(text)
        if not parsed:
            response = MessageResponse(
                text=(
                    "❌ <b>פורמט מחירון לא תקין</b>\n\n"
                    "פורמט נכון:\n"
                    "<code>מחירון בב ים</code>\n\n"
                    "📍 מוצא + יעד (קיצור או שם מלא)\n\n"
                    "📋 לחזרה לתפריט שלח 'ת'"
                ),
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        origin, destination = parsed
        estimate = PricingService.get_price_estimate(origin, destination)

        if estimate:
            text_response = PricingService.format_price_response(estimate)
        else:
            text_response = PricingService.format_not_found_response(
                origin, destination
            )

        response = MessageResponse(
            text=text_response,
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.MENU.value, {}

    # ==================== סשן 4: הגדרות חיפוש ====================

    async def _build_settings_menu(
        self, user: User
    ) -> Tuple[MessageResponse, str, dict]:
        """בניית תגובת תפריט הגדרות — משותף ל-MENU ול-SETTINGS_VIEW"""
        text, keyboard = await self.menu_service.get_settings_menu(user.id)
        response = MessageResponse(text=text, keyboard=keyboard, button_text="🛠 הגדרות")
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
            button_text="🚗 בחירת סוג רכב",
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
                text="❌ לא זיהיתי. 🚗 בחירת סוג רכב:",
                keyboard=keyboard,
                button_text="🚗 בחירת סוג רכב",
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
            button_text="🛣️ בחירת סוג נסיעה",
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
                text="❌ לא זיהיתי. 🛣️ בחירת סוג נסיעה:",
                keyboard=keyboard,
                button_text="🛣️ בחירת סוג נסיעה",
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
            button_text="📦 הצגת משלוחים",
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
                text="❌ לא זיהיתי. 📦 הצגת משלוחים — כן או לא?",
                keyboard=[["✅ כן — הצג משלוחים"], ["❌ לא — ללא משלוחים"], ["❌ ביטול"]],
                button_text="📦 הצגת משלוחים",
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
            button_text="⏰ בחירת טווח זמן",
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
                text="❌ לא זיהיתי. ⏰ בחירת טווח זמן:",
                keyboard=keyboard,
                button_text="⏰ בחירת טווח זמן",
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
            button_text="📅 חיפוש עתידי",
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
            button_text="📅 חיפוש עתידי",
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

    # ==================== סשן 5: חיפוש נסיעות ====================

    async def _handle_search_command(
        self, user: User, text: str
    ) -> Tuple[MessageResponse, str, dict]:
        """
        טיפול בפקודת חיפוש מהתפריט הראשי.

        מפרסר את הפקודה ויוצר חיפוש חדש מיידית.
        אם הפקודה היא "פ מיקום" — עובר למצב המתנה למיקום GPS.
        סשן 8: בודק מנוי פעיל לפני יצירת חיפוש.
        """
        parsed = CityAbbreviationService.parse_search_command(text)

        if not parsed:
            # פקודה לא תקינה — הצגת עזרה
            response = MessageResponse(
                text=(
                    "❌ פקודת חיפוש לא תקינה.\n\n"
                    "🔍 <b>דוגמאות לחיפוש:</b>\n"
                    "• <b>פ ים</b> — חיפוש לירושלים\n"
                    "• <b>פ בב ים</b> — מבני ברק לירושלים\n"
                    "• <b>פ א טב</b> — אזור טבריה\n"
                    "• <b>פ מיקום</b> — חיפוש לפי מיקום GPS\n\n"
                    "💡 שלח 'מילון' לרשימת קיצורי ערים"
                ),
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        # סשן 8: בדיקת מנוי פעיל — רק אחרי פרסור מוצלח של הפקודה,
        # כדי לחסוך שאילתת DB מיותרת על פקודות לא תקינות
        if not await self.subscription_service.is_subscription_active(user.id):
            response = MessageResponse(
                text=(
                    "⚠️ <b>המנוי שלך פג תוקף</b>\n\n"
                    "רכוש מנוי כדי להמשיך לחפש.\n"
                    "לחץ על כפתור 'מנוי' לפרטים."
                ),
                keyboard=[["💳 מנוי"], ["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        # חיפוש לפי מיקום — מעבר למצב המתנה לשיתוף מיקום
        if parsed.is_location_search:
            response = MessageResponse(
                text=(
                    "📍 <b>חיפוש לפי מיקום</b>\n\n"
                    "שתף את המיקום שלך כדי לחפש נסיעות באזור.\n"
                    "לחץ על סמל הצמד (📎) ובחר 'מיקום'."
                ),
                keyboard=[["❌ ביטול"]],
            )
            return response, DriverState.SEARCH_CREATE_ORIGIN.value, {}

        # חיפוש רגיל / אזורי — יצירה מיידית
        origin = parsed.origin or ""
        try:
            search = await self.search_service.create_search(
                user_id=user.id,
                origin_city=origin,
                destination_city=parsed.destination,
                is_area_search=parsed.is_area_search,
            )
        except ValidationException as e:
            response = MessageResponse(
                text=f"❌ {e.message}",
                keyboard=[["🔍 חיפושים פעילים"], ["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        # הצגת אישור + סיכום + מספר נהגים פנויים
        summary = DriverSearchService.format_search_summary(search)

        # שליפת כל החיפושים הפעילים עם ספירת נהגים לכל יעד — שאילתה אחת
        all_searches = await self.search_service.get_active_searches(user.id)
        destination_cities = [s.destination_city for s in all_searches]
        driver_counts = await self.search_service.count_available_drivers_for_destinations(
            destination_cities, exclude_user_id=user.id
        )
        available_drivers = driver_counts.get(search.destination_city, 0)

        # בניית שורת נהגים פנויים עם שמות ערים מלאים
        origin_name = search.origin_city or ""
        dest_name = search.destination_city or ""
        if origin_name and origin_name != "מיקום נוכחי":
            drivers_line = (
                f"🎲 {available_drivers} נהגים פנויים איתך "
                f"מ{escape(origin_name)} ל{escape(dest_name)}"
            )
        else:
            drivers_line = (
                f"🎲 {available_drivers} נהגים פנויים איתך "
                f"ל{escape(dest_name)}"
            )

        text_parts = [
            "✅ החיפוש נכנס למערכת!\n",
            "אני על זה בשבילך\n\n",
            f"{summary}\n\n",
            f"{drivers_line}\n\n",
            "מעכשיו, כל נסיעה רלוונטית שתתפרסם באחת מהקבוצות שלך "
            "או בבוט תקפוץ לך כאן בצ׳אט 📲\n\n",
            "📊 <b>סטטוס חיפושים</b>\n",
            "▫️ המערכת סורקת כעת עשרות מקורות...\n",
        ]

        # הצגת כל החיפושים הפעילים עם נהגים פנויים לכל חיפוש
        if all_searches:
            text_parts.append(f"\n📋 <b>חיפושים פעילים ({len(all_searches)}/{MAX_ACTIVE_SEARCHES_PER_USER})</b>\n")
            for i, s in enumerate(all_searches, 1):
                s_summary = DriverSearchService.format_search_summary(s)
                s_drivers = driver_counts.get(s.destination_city, 0)
                text_parts.append(f"{i}. {s_summary} — 🎲 {s_drivers} נהגים\n")

        response = MessageResponse(
            text="".join(text_parts),
            keyboard=[["🎯 תפריט ראשי"]],
        )
        return response, DriverState.MENU.value, {}

    async def _build_search_view(
        self, user: User
    ) -> Tuple[MessageResponse, str, dict]:
        """בניית תצוגת חיפושים פעילים — כולל נהגים פנויים ונסיעות סדרן תואמות (סשן 9)"""
        searches = await self.search_service.get_active_searches(user.id)

        if not searches:
            response = MessageResponse(
                text=(
                    "🔍 <b>חיפושים פעילים</b>\n\n"
                    "אין חיפושים פעילים כרגע.\n\n"
                    "💡 להוספת חיפוש - שלח 'פ &lt;יעד&gt;'\n"
                    "לדוגמה: 'פ ים' לחיפוש לירושלים"
                ),
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        # בניית רשימת חיפושים עם מספר נהגים פנויים לכל יעד — שאילתה אחת במקום N+1
        destination_cities = [s.destination_city for s in searches]
        driver_counts = await self.search_service.count_available_drivers_for_destinations(
            destination_cities, exclude_user_id=user.id
        )
        search_lines = []
        for i, search in enumerate(searches, 1):
            summary = DriverSearchService.format_search_summary(search)
            drivers_count = driver_counts.get(search.destination_city, 0)
            search_lines.append(f"{i}. {summary} — 🎲 {drivers_count} נהגים")

        searches_text = "\n".join(search_lines)

        # סשן 9: שליפת נסיעות סדרן תואמות לחיפושים הפעילים
        dispatcher_rides_text = await self._get_matching_dispatcher_rides(searches)

        keyboard = [["🗑 מחק חיפוש"], ["🗑 מחק הכל"]]
        keyboard.append(["🔙 חזרה לתפריט"])

        text_parts = [
            f"🔍 <b>חיפושים פעילים ({len(searches)}/{MAX_ACTIVE_SEARCHES_PER_USER})</b>\n\n"
            f"{searches_text}",
        ]
        if dispatcher_rides_text:
            text_parts.append(
                f"\n\n🚗 <b>נסיעות סדרן תואמות:</b>\n{dispatcher_rides_text}"
            )
        text_parts.append("\n\n💡 להוספת חיפוש - שלח 'פ &lt;יעד&gt;'")

        response = MessageResponse(
            text="".join(text_parts),
            keyboard=keyboard,
            button_text="🔍 ניהול חיפושים",
        )
        return response, DriverState.SEARCH_VIEW_ACTIVE.value, {}

    async def _get_matching_dispatcher_rides(
        self, searches: list
    ) -> str:
        """
        שליפת נסיעות סדרן שתואמות לחיפושים הפעילים של הנהג (סשן 9).

        Args:
            searches: רשימת חיפושים פעילים של הנהג

        Returns:
            טקסט מפורמט של נסיעות תואמות, או מחרוזת ריקה אם אין
        """
        station_service = StationService(self.db)
        seen_ids: set[int] = set()
        matching_rides = []

        for search in searches:
            rides = await station_service.get_open_rides_for_search(
                origin_city=search.origin_city if search.origin_city != "מיקום נוכחי" else None,
                destination_city=search.destination_city if search.destination_city != "אזור מיקום" else None,
            )
            for ride in rides:
                if ride.id not in seen_ids:
                    seen_ids.add(ride.id)
                    matching_rides.append(ride)

        if not matching_rides:
            return ""

        lines = []
        for i, ride in enumerate(matching_rides[:10], 1):
            lines.append(
                f"{i}. {escape(ride.origin_city)} → {escape(ride.destination_city)} | "
                f"👥 {ride.seats} | 💰 {ride.price:.0f} ₪"
            )
        return "\n".join(lines)

    async def _handle_search_view_active(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        מצב צפייה בחיפושים — ניתוב לפי בחירת כפתור.
        כולל טיפול באישור מחיקת כל החיפושים (סשן 6).
        """
        msg = message.strip()

        # סשן 6: טיפול באישור "מחק הכל" (מתוך פקודת "ממ")
        if context.get("delete_all_pending"):
            if "כן" in msg:
                count = await self.search_service.delete_all_searches(user.id)
                if count == 0:
                    response = MessageResponse(
                        text="ℹ️ אין חיפושים למחיקה.",
                        keyboard=[["🔙 חזרה לתפריט"]],
                    )
                    return response, DriverState.MENU.value, {"delete_all_pending": None}
                response = MessageResponse(
                    text=f"✅ {count} חיפושים נמחקו בהצלחה.",
                    keyboard=[["🔙 חזרה לתפריט"]],
                )
                return response, DriverState.MENU.value, {"delete_all_pending": None}
            # ביטול / כל תשובה אחרת
            response = MessageResponse(
                text="❌ המחיקה בוטלה.",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {"delete_all_pending": None}

        # חזרה לתפריט
        if "חזרה" in msg or "תפריט" in msg:
            return await self._build_main_menu(user)

        # פקודת חיפוש חדש מתוך מסך החיפושים
        if CityAbbreviationService.is_search_command(msg):
            return await self._handle_search_command(user, msg)

        # סשן 6: פקודות ניהול חיפושים מתוך מסך הצפייה
        if msg == "ע":
            return await self._handle_pause_searches(user)
        if msg == "ה":
            return await self._handle_resume_searches(user)
        if msg == "ממ":
            return self._show_delete_all_confirmation()
        if msg == "מ":
            return await self._build_search_delete_menu(user)

        # מחיקת כל החיפושים (כפתור מסך צפייה)
        if "מחק הכל" in msg:
            count = await self.search_service.delete_all_searches(user.id)
            if count == 0:
                response = MessageResponse(
                    text="ℹ️ אין חיפושים למחיקה.",
                    keyboard=[["🔙 חזרה לתפריט"]],
                )
                return response, DriverState.MENU.value, {}
            response = MessageResponse(
                text=f"✅ {count} חיפושים נמחקו בהצלחה.",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        # מחיקת חיפוש בודד — הצגת רשימה לבחירה
        if "מחק" in msg:
            return await self._build_search_delete_menu(user)

        # ברירת מחדל — רענון תצוגת חיפושים
        return await self._build_search_view(user)

    async def _build_search_delete_menu(
        self, user: User
    ) -> Tuple[MessageResponse, str, dict]:
        """בניית תפריט מחיקת חיפוש — הצגת רשימה ממוספרת לבחירה (פעילים + מושהים)"""
        searches = await self.search_service.get_non_deleted_searches(user.id)
        if not searches:
            response = MessageResponse(
                text="ℹ️ אין חיפושים למחיקה.",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        keyboard = []
        for i, search in enumerate(searches, 1):
            summary = DriverSearchService.format_search_summary(
                search, html_escape=False
            )
            keyboard.append([f"🗑 {i}. {summary}"])

        keyboard.append(["❌ ביטול"])

        response = MessageResponse(
            text="🗑 <b>בחר חיפוש למחיקה:</b>",
            keyboard=keyboard,
            button_text="🗑 מחיקת חיפוש",
        )
        # שמירת מזהי חיפושים בקונטקסט לשימוש במצב SEARCH_MANAGE
        search_ids = {str(i): search.id for i, search in enumerate(searches, 1)}
        return response, DriverState.SEARCH_MANAGE.value, {"search_ids": search_ids}

    async def _handle_search_manage(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        מצב ניהול חיפוש — מחיקת חיפוש לפי בחירת מספר.
        """
        msg = message.strip()

        # ביטול
        if "ביטול" in msg:
            return await self._build_search_view(user)

        # חזרה לתפריט
        if "חזרה" in msg or "תפריט" in msg:
            return await self._build_main_menu(user)

        # ניסיון לזהות מספר חיפוש מתוך טקסט הכפתור
        search_ids: dict[str, int] = context.get("search_ids", {})
        selected_index = self._extract_search_index(msg)

        if selected_index and str(selected_index) in search_ids:
            search_id = search_ids[str(selected_index)]
            try:
                await self.search_service.delete_search(user.id, search_id)
                response = MessageResponse(
                    text="✅ החיפוש נמחק בהצלחה.",
                    keyboard=[["🔍 חיפושים פעילים"], ["🔙 חזרה לתפריט"]],
                )
                return response, DriverState.SEARCH_VIEW_ACTIVE.value, {}
            except (NotFoundException, ValidationException) as e:
                err_msg = e.message if hasattr(e, "message") else str(e)
                response = MessageResponse(
                    text=f"❌ {err_msg}",
                    keyboard=[["🔍 חיפושים פעילים"], ["🔙 חזרה לתפריט"]],
                )
                return response, DriverState.SEARCH_VIEW_ACTIVE.value, {}

        # בחירה לא מוכרת — הצגת תפריט מחיקה מחדש
        return await self._build_search_delete_menu(user)

    @staticmethod
    def _extract_search_index(text: str) -> int | None:
        """חילוץ מספר חיפוש מטקסט כפתור (למשל '🗑 1. ...' → 1)"""
        import re
        match = re.match(r"🗑\s*(\d+)\.", text.strip())
        if match:
            return int(match.group(1))
        return None

    async def _handle_search_create_location(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        מצב המתנה למיקום GPS — מצפה לקבל קואורדינטות מיקום.

        הקואורדינטות מועברות דרך kwargs (location_lat, location_lng)
        מה-webhook handler.
        """
        location_lat: float | None = kwargs.get("location_lat")
        location_lng: float | None = kwargs.get("location_lng")

        # ביטול
        if message and "ביטול" in message.strip():
            return await self._build_main_menu(user)

        if location_lat is None or location_lng is None:
            # לא התקבל מיקום — הנחיה חוזרת
            response = MessageResponse(
                text=(
                    "📍 <b>חיפוש לפי מיקום</b>\n\n"
                    "לא התקבל מיקום.\n"
                    "שתף את המיקום שלך דרך סמל הצמד (📎) ← 'מיקום'."
                ),
                keyboard=[["❌ ביטול"]],
            )
            return response, DriverState.SEARCH_CREATE_ORIGIN.value, {}

        # יצירת חיפוש מיקום
        try:
            search = await self.search_service.create_location_search(
                user_id=user.id,
                latitude=location_lat,
                longitude=location_lng,
            )
        except ValidationException as e:
            response = MessageResponse(
                text=f"❌ {e.message}",
                keyboard=[["🔍 חיפושים פעילים"], ["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        summary = DriverSearchService.format_search_summary(search)
        active_count = await self.search_service.get_active_search_count(user.id)

        response = MessageResponse(
            text=(
                "✅ <b>חיפוש לפי מיקום נוסף!</b>\n\n"
                f"{summary}\n\n"
                f"📊 סה״כ חיפושים פעילים: {active_count}/{MAX_ACTIVE_SEARCHES_PER_USER}"
            ),
            keyboard=[["🔍 חיפושים פעילים"], ["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.MENU.value, {}

    # ==================== סשן 6: ניהול חיפושים ====================

    async def _handle_pause_searches(
        self, user: User
    ) -> Tuple[MessageResponse, str, dict]:
        """פקודת "ע" — השהיית כל החיפושים הפעילים"""
        count = await self.search_service.pause_all_searches(user.id)
        if count == 0:
            response = MessageResponse(
                text="ℹ️ אין חיפושים פעילים להשהיה.",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        response = MessageResponse(
            text=f"⏸ {count} חיפושים הושהו.\n\n💡 לחידוש — שלח 'ה'",
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.MENU.value, {}

    async def _handle_resume_searches(
        self, user: User
    ) -> Tuple[MessageResponse, str, dict]:
        """פקודת "ה" — חידוש כל החיפושים המושהים (עד למגבלת המקסימום)"""
        try:
            count = await self.search_service.resume_all_searches(user.id)
        except ValidationException as e:
            err_msg = e.message if hasattr(e, "message") else str(e)
            response = MessageResponse(
                text=f"❌ {err_msg}",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        if count == 0:
            response = MessageResponse(
                text="ℹ️ אין חיפושים מושהים לחידוש.",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {}

        # בדיקה אם נותרו חיפושים מושהים (חידוש חלקי עקב מגבלת מקסימום)
        paused_remaining = await self.search_service.get_non_deleted_searches(user.id)
        still_paused = sum(
            1 for s in paused_remaining
            if s.status == DriverSearchStatus.PAUSED.value
        )

        text = f"▶️ {count} חיפושים חודשו בהצלחה."
        if still_paused > 0:
            text += (
                f"\n\n⚠️ נותרו {still_paused} חיפושים מושהים — "
                f"המקסימום הוא {MAX_ACTIVE_SEARCHES_PER_USER} פעילים."
            )
        text += "\n\n💡 להשהיה — שלח 'ע'"

        response = MessageResponse(
            text=text,
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, DriverState.MENU.value, {}

    def _show_delete_all_confirmation(self) -> Tuple[MessageResponse, str, dict]:
        """פקודת "ממ" — אישור מחיקת כל החיפושים"""
        response = MessageResponse(
            text="⚠️ <b>האם אתה בטוח שברצונך למחוק את כל החיפושים?</b>",
            keyboard=[["✅ כן — מחק הכל"], ["❌ ביטול"]],
        )
        return response, DriverState.SEARCH_VIEW_ACTIVE.value, {"delete_all_pending": True}

    # ==================== סשן 8: מנוי ====================

    async def _build_subscription_view(
        self, user: User
    ) -> Tuple[MessageResponse, str, dict]:
        """בניית תצוגת סטטוס מנוי עם אפשרויות רכישה"""
        try:
            status_info = await self.subscription_service.get_subscription_status(
                user.id
            )
        except NotFoundException:
            response = MessageResponse(
                text="❌ שגיאה בטעינת פרטי מנוי. נסה שוב מאוחר יותר.",
            )
            return response, DriverState.MENU.value, {}

        # בניית טקסט סטטוס — שימוש ב-is_active כדי לא להציג "פגה" בשעות האחרונות
        if status_info["is_trial"]:
            days = status_info["days_remaining"]
            if status_info["is_active"]:
                if days is not None and days > 0:
                    status_text = f"🆓 <b>שבוע ניסיון</b> — נותרו {days} ימים"
                else:
                    # פחות מ-24 שעות אבל עדיין פעיל
                    status_text = "🆓 <b>שבוע ניסיון</b> — יום אחרון!"
            else:
                status_text = "⚠️ <b>תקופת הניסיון פגה</b>"
        elif status_info["status"] == DriverSubscriptionStatus.ACTIVE.value:
            days = status_info["days_remaining"]
            if status_info["is_active"]:
                if days is not None and days > 0:
                    expires = status_info["subscription_expires_at"]
                    expires_str = expires.strftime("%d/%m/%Y") if expires else ""
                    status_text = (
                        f"✅ <b>מנוי פעיל</b>\n"
                        f"נותרו {days} ימים (עד {expires_str})"
                    )
                elif days is not None:
                    # פחות מ-24 שעות אבל עדיין פעיל
                    status_text = "✅ <b>מנוי פעיל</b> — יום אחרון!"
                else:
                    status_text = "✅ <b>מנוי פעיל</b>"
            else:
                # DB עדיין ACTIVE אבל בפועל פג — Celery עדיין לא עדכן
                status_text = "⚠️ <b>המנוי פג תוקף</b>"
        elif status_info["status"] == DriverSubscriptionStatus.EXPIRED.value:
            status_text = "⚠️ <b>המנוי פג תוקף</b>"
        elif status_info["status"] == DriverSubscriptionStatus.PAUSED.value:
            status_text = "⏸ <b>המנוי מושהה</b>"
        elif status_info["status"] == DriverSubscriptionStatus.CANCELLED.value:
            status_text = "❌ <b>המנוי בוטל</b>"
        else:
            status_text = "📋 סטטוס לא ידוע"

        text = (
            "💳 <b>מנוי iDriver</b>\n\n"
            f"{status_text}\n\n"
            "📦 <b>חבילות זמינות:</b>\n"
            "• חודש אחד — 80 ש\"ח + מע\"מ\n"
            "• חודשיים — 160 ש\"ח + מע\"מ\n"
            "• 3 חודשים — 240 ש\"ח + מע\"מ\n\n"
            "בחר חבילה לרכישה:"
        )

        keyboard = [
            ["📦 חודש אחד"],
            ["📦 חודשיים"],
            ["📦 3 חודשים"],
            ["🔙 חזרה לתפריט"],
        ]

        response = MessageResponse(text=text, keyboard=keyboard, button_text="💳 רכישת מנוי")
        return response, DriverState.SUBSCRIPTION_VIEW.value, {}

    async def _handle_subscription_view(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        מצב צפייה במנוי — טיפול בבחירת חבילה או חזרה לתפריט.
        """
        msg = message.strip()

        # חזרה לתפריט
        if "חזרה" in msg or "תפריט" in msg or msg in ("ת", "חזור"):
            return await self._build_main_menu(user)

        # בחירת חבילה
        from app.domain.services.driver_subscription_service import (
            parse_subscription_choice,
            months_to_label,
        )
        months = parse_subscription_choice(msg)
        if months is None:
            response = MessageResponse(
                text="❌ לא זיהיתי. 📦 בחירת חבילת מנוי:",
                keyboard=[
                    ["📦 חודש אחד"],
                    ["📦 חודשיים"],
                    ["📦 3 חודשים"],
                    ["🔙 חזרה לתפריט"],
                ],
            )
            return response, DriverState.SUBSCRIPTION_VIEW.value, {}

        # מעבר לתשלום — הצגת פרטי PayBox
        from app.domain.services.driver_subscription_service import SUBSCRIPTION_PRICES
        from app.core.config import settings

        months_label = months_to_label(months)
        price = SUBSCRIPTION_PRICES.get(months, 0)
        paybox_number = settings.PAYBOX_PHONE_NUMBER or "לא הוגדר"

        response = MessageResponse(
            text=(
                f"💳 <b>תשלום מנוי — {months_label}</b>\n\n"
                f"💰 מחיר: <b>{price} ש\"ח + מע\"מ</b>\n\n"
                f"📱 <b>מספר פייבוקס לתשלום:</b>\n"
                f"<code>{paybox_number}</code>\n\n"
                "לאחר התשלום, שלח צילום מסך של אישור התשלום."
            ),
            keyboard=[
                ["❌ ביטול"],
            ],
        )
        return (
            response,
            DriverState.SUBSCRIPTION_PURCHASE.value,
            {"subscription_months": str(months)},
        )

    async def _handle_subscription_purchase(
        self, user: User, message: str, context: dict, **kwargs: object
    ) -> Tuple[MessageResponse, str, dict]:
        """
        מצב תשלום מנוי — קבלת צילום מסך תשלום או ביטול.
        """
        msg = message.strip()
        photo_file_id: str | None = kwargs.get("photo_file_id")

        # ביטול
        if "ביטול" in msg or "חזרה" in msg:
            return await self._build_subscription_view(user)

        # קבלת צילום מסך תשלום
        if photo_file_id:
            months_str = context.get("subscription_months", "1")
            try:
                months = int(months_str)
            except (ValueError, TypeError):
                months = 1

            from app.domain.services.driver_subscription_service import months_to_label
            months_label = months_to_label(months)

            # שליחת התראה לאדמין עם כפתור אישור
            from app.domain.services.admin_notification_service import (
                AdminNotificationService,
            )

            full_name = user.full_name or user.name or "לא צוין"
            await AdminNotificationService.notify_subscription_payment(
                user_id=user.id,
                full_name=full_name,
                months=months,
                months_label=months_label,
                screenshot_file_id=photo_file_id,
                platform=self.platform,
                role="driver",
            )

            response = MessageResponse(
                text=(
                    "✅ <b>אישור התשלום התקבל!</b>\n\n"
                    f"📦 חבילה: {months_label}\n\n"
                    "הבקשה הועברה לאישור הנהלה.\n"
                    "המנוי ייפתח לאחר אישור התשלום.\n\n"
                    "⏳ זמן טיפול: עד 24 שעות."
                ),
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, DriverState.MENU.value, {
                "subscription_months": None,
            }

        # לא נשלחה תמונה — בקשה לצילום מסך
        response = MessageResponse(
            text="📸 אנא שלח צילום מסך של אישור התשלום, או לחץ 'ביטול'.",
            keyboard=[["❌ ביטול"]],
        )
        return response, DriverState.SUBSCRIPTION_PURCHASE.value, None

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
