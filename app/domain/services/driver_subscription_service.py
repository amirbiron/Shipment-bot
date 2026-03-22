"""
שירות מנויי נהג (iDriver) — סשן 8

מנהל את מחזור חיי המנוי של נהג:
- הפעלת תקופת ניסיון (7 ימים) בסיום רישום
- רכישת מנוי (חודשי / רב-חודשי)
- בדיקת תקינות מנוי לפני חיפוש
- שליפת סטטוס מנוי לתצוגה בתפריט
- איתור מנויים שעומדים לפוג (לתזכורות Celery)
"""
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.driver_profile import (
    DriverProfile,
    DriverSubscriptionStatus,
)
from app.core.logging import get_logger
from app.core.exceptions import ValidationException, NotFoundException

logger = get_logger(__name__)

# קבועים
TRIAL_DURATION_DAYS = 7
SUBSCRIPTION_MONTH_DAYS = 30
# ימים לפני תפוגה לשליחת תזכורת
REMINDER_DAYS_BEFORE = 1

# מחירון מנויים (ש"ח לפני מע"מ)
SUBSCRIPTION_PRICES: dict[int, int] = {
    1: 80,    # חודש אחד
    2: 160,   # חודשיים
    3: 240,   # 3 חודשים
}


def parse_subscription_choice(text: str) -> int | None:
    """מיפוי בחירת חבילה לחודשים — משותף לנהג ושליח."""
    _CHOICE_MAP = {
        "📦 3 חודשים": 3,
        "📦 חודשיים": 2,
        "📦 חודש אחד": 1,
    }
    for label, months in _CHOICE_MAP.items():
        if text == label:
            return months
    if "3 חודשים" in text:
        return 3
    if "חודשיים" in text:
        return 2
    if "חודש אחד" in text:
        return 1
    return None


def months_to_label(months: int) -> str:
    """מיפוי מספר חודשים לתווית — משותף לנהג ושליח."""
    labels = {
        1: "חודש אחד",
        2: "חודשיים",
        3: "3 חודשים",
    }
    return labels.get(months, f"{months} חודשים")


class DriverSubscriptionService:
    """שירות מנוי נהג — הפעלה, רכישה, בדיקת תקינות ותזכורות"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def activate_trial(
        self, user_id: int, *, commit: bool = True
    ) -> DriverProfile:
        """
        הפעלת תקופת ניסיון (7 ימים).

        נקרא אוטומטית בסיום רישום (save_dress_code).
        אם כבר הופעלה תקופת ניסיון — לא מאפסת.

        Args:
            user_id: מזהה המשתמש
            commit: האם לבצע commit. False כשנקרא בתוך טרנזקציה חיצונית.

        Returns:
            הפרופיל המעודכן

        Raises:
            NotFoundException: פרופיל נהג לא נמצא
        """
        profile = await self._get_profile(user_id)
        if not profile:
            raise NotFoundException("DriverProfile", user_id)

        # מניעת הפעלה כפולה
        if profile.trial_starts_at is not None:
            logger.info(
                "ניסיון הפעלת trial כפול — מתעלם",
                extra_data={"user_id": user_id},
            )
            return profile

        now = datetime.utcnow()
        profile.trial_starts_at = now
        profile.trial_expires_at = now + timedelta(days=TRIAL_DURATION_DAYS)
        profile.subscription_status = DriverSubscriptionStatus.TRIAL.value
        profile.updated_at = now

        if commit:
            await self.db.commit()
            await self.db.refresh(profile)

        logger.info(
            "תקופת ניסיון הופעלה",
            extra_data={
                "user_id": user_id,
                "expires_at": profile.trial_expires_at.isoformat(),
            },
        )
        return profile

    async def purchase_subscription(
        self, user_id: int, months: int = 1
    ) -> DriverProfile:
        """
        רכישת מנוי (חודשי / רב-חודשי).

        מאריך את תאריך התפוגה מהיום או מתאריך התפוגה הנוכחי
        (הגדול מביניהם), כדי לא לגזול ימים ששולמו.

        Args:
            user_id: מזהה המשתמש
            months: מספר חודשים לרכישה (ברירת מחדל: 1)

        Returns:
            הפרופיל המעודכן

        Raises:
            NotFoundException: פרופיל נהג לא נמצא
            ValidationException: מספר חודשים לא תקין
        """
        if months < 1 or months > 12:
            raise ValidationException("מספר חודשים חייב להיות בין 1 ל-12")

        profile = await self._get_profile(user_id)
        if not profile:
            raise NotFoundException("DriverProfile", user_id)

        now = datetime.utcnow()

        # חישוב תאריך התחלה — מהיום או מתום המנוי הנוכחי (הגדול)
        current_end = profile.subscription_expires_at
        if current_end is None or current_end < now:
            # גם trial שפג — מתחילים מהיום
            if profile.trial_expires_at is not None and profile.trial_expires_at > now:
                start_from = profile.trial_expires_at
            else:
                start_from = now
        else:
            start_from = current_end

        new_end = start_from + timedelta(days=months * SUBSCRIPTION_MONTH_DAYS)

        profile.subscription_status = DriverSubscriptionStatus.ACTIVE.value
        profile.subscription_start_at = now
        profile.subscription_expires_at = new_end
        profile.updated_at = now

        await self.db.commit()
        await self.db.refresh(profile)

        logger.info(
            "מנוי נרכש",
            extra_data={
                "user_id": user_id,
                "months": months,
                "expires_at": new_end.isoformat(),
            },
        )
        return profile

    @staticmethod
    def _is_profile_active(profile: DriverProfile) -> bool:
        """בדיקת גישה פעילה על פרופיל שכבר נשלף — ללא שאילתת DB נוספת."""
        now = datetime.utcnow()
        status = profile.subscription_status

        # מנוי פעיל ששולם
        if status == DriverSubscriptionStatus.ACTIVE.value:
            if profile.subscription_expires_at is not None:
                return profile.subscription_expires_at > now
            # אין תאריך תפוגה — פרופיל לא תקין, לא מעניקים גישה
            return False

        # תקופת ניסיון
        if status == DriverSubscriptionStatus.TRIAL.value:
            if profile.trial_expires_at is not None:
                return profile.trial_expires_at > now
            # אין תאריך תפוגה — פרופיל לא תקין, לא מעניקים גישה
            return False

        # כל סטטוס אחר (expired, paused, cancelled) = לא פעיל
        return False

    async def is_subscription_active(self, user_id: int) -> bool:
        """
        בדיקה האם למנוי יש גישה פעילה (trial או מנוי ששולם).

        Args:
            user_id: מזהה המשתמש

        Returns:
            True אם המנוי פעיל
        """
        profile = await self._get_profile(user_id)
        if not profile:
            return False

        return self._is_profile_active(profile)

    async def get_subscription_status(self, user_id: int) -> dict:
        """
        שליפת סטטוס מנוי מפורט לתצוגה.

        Args:
            user_id: מזהה המשתמש

        Returns:
            dict עם פרטי המנוי

        Raises:
            NotFoundException: פרופיל נהג לא נמצא
        """
        profile = await self._get_profile(user_id)
        if not profile:
            raise NotFoundException("DriverProfile", user_id)

        now = datetime.utcnow()
        # שימוש בפרופיל שכבר נשלף — ללא שאילתת DB כפולה
        is_active = self._is_profile_active(profile)

        # חישוב ימים שנותרו
        days_remaining: int | None = None
        if profile.subscription_status == DriverSubscriptionStatus.ACTIVE.value:
            if profile.subscription_expires_at is not None:
                delta = profile.subscription_expires_at - now
                days_remaining = max(0, delta.days)
        elif profile.subscription_status == DriverSubscriptionStatus.TRIAL.value:
            if profile.trial_expires_at is not None:
                delta = profile.trial_expires_at - now
                days_remaining = max(0, delta.days)

        return {
            "status": profile.subscription_status,
            "is_active": is_active,
            "is_trial": profile.subscription_status == DriverSubscriptionStatus.TRIAL.value,
            "days_remaining": days_remaining,
            "trial_expires_at": profile.trial_expires_at,
            "subscription_expires_at": profile.subscription_expires_at,
        }

    async def check_expiring_subscriptions(
        self,
    ) -> list[DriverProfile]:
        """
        איתור מנויים שעומדים לפוג תוך יום אחד.

        משמש את ה-Celery task לשליחת תזכורות.

        Returns:
            רשימת פרופילים עם מנוי שעומד לפוג
        """
        now = datetime.utcnow()
        reminder_cutoff = now + timedelta(days=REMINDER_DAYS_BEFORE)

        # מנויים ששולמו — פגים תוך יום
        paid_result = await self.db.execute(
            select(DriverProfile).where(
                DriverProfile.subscription_status == DriverSubscriptionStatus.ACTIVE.value,
                DriverProfile.subscription_expires_at.isnot(None),
                DriverProfile.subscription_expires_at > now,
                DriverProfile.subscription_expires_at <= reminder_cutoff,
            )
        )
        paid_expiring = list(paid_result.scalars().all())

        # trial שפג תוך יום
        trial_result = await self.db.execute(
            select(DriverProfile).where(
                DriverProfile.subscription_status == DriverSubscriptionStatus.TRIAL.value,
                DriverProfile.trial_expires_at.isnot(None),
                DriverProfile.trial_expires_at > now,
                DriverProfile.trial_expires_at <= reminder_cutoff,
            )
        )
        trial_expiring = list(trial_result.scalars().all())

        return paid_expiring + trial_expiring

    async def expire_lapsed_subscriptions(self) -> int:
        """
        עדכון סטטוס למנויים שפג תוקפם.

        משמש את ה-Celery task — רץ יומית.

        Returns:
            מספר מנויים שעודכנו ל-EXPIRED
        """
        now = datetime.utcnow()
        expired_count = 0

        # מנויים ששולמו שפגו
        paid_result = await self.db.execute(
            select(DriverProfile).where(
                DriverProfile.subscription_status == DriverSubscriptionStatus.ACTIVE.value,
                DriverProfile.subscription_expires_at.isnot(None),
                DriverProfile.subscription_expires_at <= now,
            )
        )
        for profile in paid_result.scalars().all():
            profile.subscription_status = DriverSubscriptionStatus.EXPIRED.value
            profile.updated_at = now
            expired_count += 1

        # trial שפג
        trial_result = await self.db.execute(
            select(DriverProfile).where(
                DriverProfile.subscription_status == DriverSubscriptionStatus.TRIAL.value,
                DriverProfile.trial_expires_at.isnot(None),
                DriverProfile.trial_expires_at <= now,
            )
        )
        for profile in trial_result.scalars().all():
            profile.subscription_status = DriverSubscriptionStatus.EXPIRED.value
            profile.updated_at = now
            expired_count += 1

        if expired_count > 0:
            await self.db.commit()
            logger.info(
                "מנויים סומנו כפגי תוקף",
                extra_data={"expired_count": expired_count},
            )

        return expired_count

    # ==================== מתודות פנימיות ====================

    async def _get_profile(self, user_id: int) -> DriverProfile | None:
        """שליפת פרופיל נהג"""
        result = await self.db.execute(
            select(DriverProfile).where(DriverProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()
