"""
שירות תפריט נהג (iDriver) — סשן 4

מנהל את התפריט הראשי של הנהג ואת הגדרות החיפוש:
- בניית הודעת תפריט ראשי עם סטטוס מנוי והגדרות נוכחיות
- בניית תפריט הגדרות חיפוש
- עדכון הגדרות בודדות (סוג רכב, סוג נסיעה, משלוחים, מסגרת זמן, עתידי בלבד)
"""
from datetime import datetime, time
from html import escape
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.driver_profile import (
    DriverProfile,
    VehicleCategory,
    DriverSubscriptionStatus,
)
from app.db.models.driver_search_settings import (
    DriverSearchSettings,
    TripTypeFilter,
    UpcomingTimeframe,
)
from app.schemas.driver import DriverSearchSettingsUpdate
from app.core.logging import get_logger
from app.core.exceptions import NotFoundException, ValidationException

logger = get_logger(__name__)


# ============================================================================
# מיפוי enum → תווית עברית
# ============================================================================

VEHICLE_TYPE_LABELS: dict[str, str] = {
    VehicleCategory.FOUR_SEATER.value: "פרטי 4 מקומות",
    VehicleCategory.CAR.value: "רכב סטנדרטי",
    VehicleCategory.VAN.value: "וואן / פורגון",
    VehicleCategory.SEVEN_SEATER.value: "7 מקומות (מיניוואן)",
    VehicleCategory.EIGHT_PLUS.value: "מעל 8 מקומות",
    VehicleCategory.TRUCK.value: "משאית",
    VehicleCategory.MOTORCYCLE.value: "אופנוע",
}

# מיפוי הפוך — מטקסט כפתור לערך enum
VEHICLE_TYPE_BY_LABEL: dict[str, str] = {v: k for k, v in VEHICLE_TYPE_LABELS.items()}

TRIP_TYPE_LABELS: dict[str, str] = {
    TripTypeFilter.SHORT_DISTANCE.value: "מתחת ל 100 ש״ח פנימיות וקצרות",
    TripTypeFilter.MEDIUM_DISTANCE.value: "טווח בינוני (15-50 ק״מ)",
    TripTypeFilter.LONG_DISTANCE.value: "מעל 100 ש״ח פנימיות ובינעירוני",
    TripTypeFilter.RIDES.value: "נסיעות נוסעים בלבד",
    TripTypeFilter.ANY_DISTANCE.value: "כל סוגי הנסיעות",
}

TRIP_TYPE_BY_LABEL: dict[str, str] = {v: k for k, v in TRIP_TYPE_LABELS.items()}

TIMEFRAME_LABELS: dict[str, str] = {
    UpcomingTimeframe.ONE_HOUR.value: "לשעה הקרובה",
    UpcomingTimeframe.TWO_HOURS.value: "לשעתיים הקרובות",
    UpcomingTimeframe.FIVE_HOURS.value: "5 שעות הקרובות",
    UpcomingTimeframe.ALL.value: "הצג הכל",
}

TIMEFRAME_BY_LABEL: dict[str, str] = {v: k for k, v in TIMEFRAME_LABELS.items()}


def _get_greeting() -> str:
    """ברכה לפי שעה ביום"""
    hour = datetime.utcnow().hour
    # UTC+2/+3 לישראל — מוסיפים 2 כהערכה
    local_hour = (hour + 2) % 24
    if 5 <= local_hour < 12:
        return "בוקר טוב"
    if 12 <= local_hour < 17:
        return "צהריים טובים"
    if 17 <= local_hour < 21:
        return "ערב טוב"
    return "לילה טוב"


class DriverMenuService:
    """שירות תפריט נהג — בניית הודעות תפריט ועדכון הגדרות"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ==================== תפריט ראשי ====================

    async def get_main_menu(self, user_id: int) -> tuple[str, list[list[str]]]:
        """
        בניית הודעת תפריט ראשי עם סטטוס מנוי והגדרות נוכחיות.

        Args:
            user_id: מזהה המשתמש

        Returns:
            tuple של (טקסט הודעה, מקלדת כפתורים)

        Raises:
            NotFoundException: פרופיל נהג לא נמצא
        """
        # שליפה מאוחדת: profile + user בשאילתה אחת (מונע N+1)
        # LEFT JOIN כדי שפרופיל ללא user לא ייעלם — שומר על fallback מקורי
        from app.db.models.user import User
        result = await self.db.execute(
            select(DriverProfile, User)
            .outerjoin(User, User.id == DriverProfile.user_id)
            .where(DriverProfile.user_id == user_id)
        )
        row = result.one_or_none()
        if not row:
            raise NotFoundException("DriverProfile", user_id)
        profile, user = row

        settings = await self._get_or_create_settings(user_id)

        name = (user.full_name or user.name or "לא צוין") if user else "לא צוין"

        greeting = _get_greeting()

        # סטטוס מנוי
        subscription_line = self._format_subscription_status(profile)

        # הגדרות נוכחיות
        vehicle_label = VEHICLE_TYPE_LABELS.get(
            settings.vehicle_type_filter, settings.vehicle_type_filter or "לא הוגדר"
        )
        trip_label = TRIP_TYPE_LABELS.get(
            settings.trip_type_filter, settings.trip_type_filter or "לא הוגדר"
        )
        deliveries_label = "כן" if settings.show_deliveries else "לא"
        timeframe_label = TIMEFRAME_LABELS.get(
            settings.upcoming_timeframe, settings.upcoming_timeframe or "לא הוגדר"
        )
        future_label = "לא הוגדר"
        if settings.future_only_enabled and settings.future_only_start_time:
            future_label = settings.future_only_start_time.strftime("%H:%M")
        elif settings.future_only_enabled:
            future_label = "מופעל"

        text = (
            f"▪️ {escape(greeting)} {escape(name)} ▪️\n"
            f"{subscription_line}\n\n"
            f"🚙 | סוג רכב - {escape(vehicle_label)}\n"
            f"🛣 | סוג נסיעה - {escape(trip_label)}\n"
            f"💌 | להציג משלוחים - {escape(deliveries_label)}\n"
            f"🕐 | עתידיות קרובות - {escape(timeframe_label)}\n"
            f"📅 | הגדר כחיפוש עתידי - {escape(future_label)}\n\n"
            "📋 בחר אפשרות מהתפריט:"
        )

        keyboard = [
            ["🛠 הגדרות חיפוש"],
            ["🔍 חיפושים פעילים"],
            ["💳 מנוי"],
            ["📖 הוראות שימוש"],
        ]

        return text, keyboard

    # ==================== תפריט הגדרות ====================

    async def get_settings_menu(self, user_id: int) -> tuple[str, list[list[str]]]:
        """
        בניית תפריט הגדרות חיפוש עם ערכים נוכחיים.

        Args:
            user_id: מזהה המשתמש

        Returns:
            tuple של (טקסט הודעה, מקלדת כפתורים)
        """
        settings = await self._get_or_create_settings(user_id)

        vehicle_label = VEHICLE_TYPE_LABELS.get(
            settings.vehicle_type_filter, settings.vehicle_type_filter or "לא הוגדר"
        )
        trip_label = TRIP_TYPE_LABELS.get(
            settings.trip_type_filter, settings.trip_type_filter or "לא הוגדר"
        )
        deliveries_label = "כן ✅" if settings.show_deliveries else "לא ❌"
        timeframe_label = TIMEFRAME_LABELS.get(
            settings.upcoming_timeframe, settings.upcoming_timeframe or "לא הוגדר"
        )
        future_label = "כבוי"
        if settings.future_only_enabled and settings.future_only_start_time:
            future_label = f"משעה {settings.future_only_start_time.strftime('%H:%M')}"
        elif settings.future_only_enabled:
            future_label = "מופעל"

        text = (
            "🛠 <b>הגדרות חיפוש</b>\n\n"
            f"🚙 סוג רכב: {escape(vehicle_label)}\n"
            f"🛣 סוג נסיעה: {escape(trip_label)}\n"
            f"💌 הצגת משלוחים: {escape(deliveries_label)}\n"
            f"🕐 מסגרת זמן: {escape(timeframe_label)}\n"
            f"📅 חיפוש עתידי: {escape(future_label)}\n\n"
            "בחר הגדרה לשינוי:"
        )

        keyboard = [
            ["🚙 סוג רכב"],
            ["🛣 סוג נסיעה"],
            ["💌 הצגת משלוחים"],
            ["🕐 מסגרת זמן"],
            ["📅 חיפוש עתידי"],
            ["🔙 חזרה לתפריט"],
        ]

        return text, keyboard

    # ==================== עדכון הגדרות ====================

    async def update_vehicle_type(self, user_id: int, vehicle_type: str) -> str:
        """
        עדכון סוג רכב.

        Args:
            user_id: מזהה המשתמש
            vehicle_type: ערך מ-VehicleCategory

        Returns:
            התווית העברית של הערך שנשמר

        Raises:
            ValidationException: ערך לא תקין
        """
        update = DriverSearchSettingsUpdate(vehicle_type_filter=vehicle_type)
        settings = await self._get_or_create_settings(user_id)

        # ולידציה צולבת מול ערכים קיימים
        update.validate_against_existing(
            existing_future_only_enabled=settings.future_only_enabled,
            existing_upcoming_timeframe=settings.upcoming_timeframe,
            existing_future_only_start_time=settings.future_only_start_time,
        )

        settings.vehicle_type_filter = update.vehicle_type_filter
        await self.db.commit()

        label = VEHICLE_TYPE_LABELS.get(vehicle_type, vehicle_type)
        logger.info(
            "סוג רכב עודכן",
            extra_data={"user_id": user_id, "vehicle_type": vehicle_type},
        )
        return label

    async def update_trip_type(self, user_id: int, trip_type: str) -> str:
        """
        עדכון סוג נסיעה.

        Args:
            user_id: מזהה המשתמש
            trip_type: ערך מ-TripTypeFilter

        Returns:
            התווית העברית של הערך שנשמר

        Raises:
            ValidationException: ערך לא תקין
        """
        update = DriverSearchSettingsUpdate(trip_type_filter=trip_type)
        settings = await self._get_or_create_settings(user_id)

        # ולידציה צולבת מול ערכים קיימים
        update.validate_against_existing(
            existing_future_only_enabled=settings.future_only_enabled,
            existing_upcoming_timeframe=settings.upcoming_timeframe,
            existing_future_only_start_time=settings.future_only_start_time,
        )

        settings.trip_type_filter = update.trip_type_filter
        await self.db.commit()

        label = TRIP_TYPE_LABELS.get(trip_type, trip_type)
        logger.info(
            "סוג נסיעה עודכן",
            extra_data={"user_id": user_id, "trip_type": trip_type},
        )
        return label

    async def update_show_deliveries(self, user_id: int, show: bool) -> bool:
        """
        עדכון הצגת משלוחים.

        Args:
            user_id: מזהה המשתמש
            show: האם להציג משלוחים

        Returns:
            הערך שנשמר
        """
        update = DriverSearchSettingsUpdate(show_deliveries=show)
        settings = await self._get_or_create_settings(user_id)

        # ולידציה צולבת מול ערכים קיימים
        update.validate_against_existing(
            existing_future_only_enabled=settings.future_only_enabled,
            existing_upcoming_timeframe=settings.upcoming_timeframe,
            existing_future_only_start_time=settings.future_only_start_time,
        )

        settings.show_deliveries = update.show_deliveries
        await self.db.commit()

        logger.info(
            "הצגת משלוחים עודכנה",
            extra_data={"user_id": user_id, "show_deliveries": show},
        )
        return show

    async def update_timeframe(self, user_id: int, timeframe: str) -> str:
        """
        עדכון מסגרת זמן.

        Args:
            user_id: מזהה המשתמש
            timeframe: ערך מ-UpcomingTimeframe

        Returns:
            התווית העברית של הערך שנשמר

        Raises:
            ValidationException: ערך לא תקין
        """
        settings = await self._get_or_create_settings(user_id)

        # אם שינו מסגרת זמן ל-לא-ALL, מכבים future_only אוטומטית
        auto_disable_future = (
            timeframe != UpcomingTimeframe.ALL.value
            and settings.future_only_enabled
        )
        update = DriverSearchSettingsUpdate(
            upcoming_timeframe=timeframe,
            future_only_enabled=False if auto_disable_future else None,
        )

        # ולידציה צולבת מול ערכים קיימים
        update.validate_against_existing(
            existing_future_only_enabled=settings.future_only_enabled,
            existing_upcoming_timeframe=settings.upcoming_timeframe,
            existing_future_only_start_time=settings.future_only_start_time,
        )

        settings.upcoming_timeframe = update.upcoming_timeframe

        if auto_disable_future:
            settings.future_only_enabled = False
            settings.future_only_start_time = None
            logger.info(
                "כיבוי אוטומטי של חיפוש עתידי — מסגרת זמן השתנתה",
                extra_data={"user_id": user_id, "timeframe": timeframe},
            )

        await self.db.commit()

        label = TIMEFRAME_LABELS.get(timeframe, timeframe)
        logger.info(
            "מסגרת זמן עודכנה",
            extra_data={"user_id": user_id, "timeframe": timeframe},
        )
        return label

    async def update_future_only(
        self, user_id: int, enabled: bool, start_time: time | None = None
    ) -> tuple[bool, time | None]:
        """
        עדכון מצב חיפוש עתידי בלבד.

        Args:
            user_id: מזהה המשתמש
            enabled: האם להפעיל חיפוש עתידי
            start_time: שעת התחלה (חובה אם enabled=True)

        Returns:
            tuple של (האם מופעל, שעת התחלה)

        Raises:
            ValidationException: ערכים לא תקינים (חסרה שעה, מסגרת זמן לא מתאימה)
        """
        update = DriverSearchSettingsUpdate(
            future_only_enabled=enabled,
            future_only_start_time=start_time,
        )
        settings = await self._get_or_create_settings(user_id)

        # ולידציה צולבת מול ערכים קיימים
        update.validate_against_existing(
            existing_future_only_enabled=settings.future_only_enabled,
            existing_upcoming_timeframe=settings.upcoming_timeframe,
            existing_future_only_start_time=settings.future_only_start_time,
        )

        settings.future_only_enabled = enabled
        settings.future_only_start_time = start_time
        await self.db.commit()

        logger.info(
            "חיפוש עתידי עודכן",
            extra_data={
                "user_id": user_id,
                "enabled": enabled,
                "start_time": start_time.isoformat() if start_time else None,
            },
        )
        return enabled, start_time

    # ==================== מתודות פנימיות ====================

    async def _get_profile(self, user_id: int) -> DriverProfile | None:
        """שליפת פרופיל נהג"""
        result = await self.db.execute(
            select(DriverProfile).where(DriverProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def _get_or_create_settings(self, user_id: int) -> DriverSearchSettings:
        """שליפת הגדרות חיפוש או יצירת ברירת מחדל"""
        result = await self.db.execute(
            select(DriverSearchSettings).where(
                DriverSearchSettings.user_id == user_id
            )
        )
        settings = result.scalar_one_or_none()
        if settings:
            return settings

        settings = DriverSearchSettings(user_id=user_id)
        self.db.add(settings)
        await self.db.commit()
        await self.db.refresh(settings)

        logger.info(
            "הגדרות חיפוש ברירת מחדל נוצרו",
            extra_data={"user_id": user_id},
        )
        return settings

    @staticmethod
    def _format_subscription_status(profile: DriverProfile) -> str:
        """פורמט סטטוס מנוי לתצוגה"""
        status = profile.subscription_status
        if status == DriverSubscriptionStatus.TRIAL.value:
            if profile.trial_expires_at:
                expires = profile.trial_expires_at.strftime("%d/%m/%Y")
                return f"🆓 שבוע ניסיון — עד {expires}"
            return "🆓 שבוע ניסיון"

        if status == DriverSubscriptionStatus.ACTIVE.value:
            if profile.subscription_expires_at:
                expires = profile.subscription_expires_at.strftime("%d/%m/%Y")
                return f"✅ מנוי פעיל עד {expires}"
            return "✅ מנוי פעיל"

        if status == DriverSubscriptionStatus.EXPIRED.value:
            return "⚠️ המנוי פג — חדש את המנוי כדי להמשיך לחפש"

        if status == DriverSubscriptionStatus.PAUSED.value:
            return "⏸ המנוי מושהה"

        if status == DriverSubscriptionStatus.CANCELLED.value:
            return "❌ המנוי בוטל"

        return "📋 סטטוס לא ידוע"
