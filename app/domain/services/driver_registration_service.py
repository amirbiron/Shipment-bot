"""
שירות רישום נהג (iDriver) — סשן 2

מנהל את כל שלבי הרישום:
1. שם מלא
2. תאריך לידה
3. תיאור רכב
4. קוד לבוש (עם ניתוב לאימות או תפריט)
"""
from datetime import date, datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.user import User
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
)
from app.core.validation import NameValidator, TextSanitizer
from app.core.logging import get_logger
from app.core.exceptions import ValidationException, NotFoundException
from app.domain.services.driver_subscription_service import DriverSubscriptionService

logger = get_logger(__name__)

# קודי לבוש שדורשים אימות (זרם חרדי)
HAREDI_DRESS_CODES = {
    DressCode.HASSIDIC.value,
    DressCode.ULTRA_ORTHODOX.value,
    DressCode.MODERN_ORTHODOX.value,
}


class DriverRegistrationService:
    """שירות רישום נהג — לוגיקה עסקית בלבד, ללא ניהול מצבים"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save_name(self, user_id: int, name: str) -> str:
        """
        שמירת שם מלא ב-User.full_name.

        Args:
            user_id: מזהה המשתמש
            name: שם מלא

        Returns:
            השם לאחר ולידציה וסניטציה

        Raises:
            ValidationException: שם לא תקין
            NotFoundException: משתמש לא נמצא
        """
        is_valid, error = NameValidator.validate(name)
        if not is_valid:
            raise ValidationException(error or "שם לא תקין", field="name")

        sanitized_name = TextSanitizer.sanitize(
            name.strip(), max_length=NameValidator.MAX_LENGTH
        )

        user = await self._get_user(user_id)
        user.full_name = sanitized_name
        await self.db.commit()

        logger.info(
            "שם נהג נשמר",
            extra_data={"user_id": user_id, "name_length": len(sanitized_name)},
        )
        return sanitized_name

    async def save_birth_date(self, user_id: int, date_str: str) -> tuple[date, int]:
        """
        ולידציית תאריך לידה, חישוב גיל ושמירה ב-DriverProfile.

        Args:
            user_id: מזהה המשתמש
            date_str: תאריך בפורמט dd/mm/yyyy

        Returns:
            tuple של (תאריך לידה, גיל)

        Raises:
            ValidationException: פורמט או טווח גיל לא תקין
        """
        birth_date = self._parse_date(date_str)
        age = self._calculate_age(birth_date)

        if age < 16:
            raise ValidationException("גיל מינימלי להרשמה הוא 16", field="birth_date")
        if age > 99:
            raise ValidationException("גיל מקסימלי הוא 99", field="birth_date")

        profile = await self._get_or_create_profile(user_id)
        profile.birth_date = birth_date
        await self.db.commit()

        logger.info(
            "תאריך לידה נשמר",
            extra_data={"user_id": user_id, "age": age},
        )
        return birth_date, age

    async def save_vehicle(self, user_id: int, vehicle_desc: str) -> str:
        """
        שמירת תיאור רכב.

        Args:
            user_id: מזהה המשתמש
            vehicle_desc: תיאור חופשי (לדוגמה: "סיינה 2025 חדישה")

        Returns:
            התיאור לאחר סניטציה

        Raises:
            ValidationException: תיאור לא תקין
        """
        vehicle_desc = vehicle_desc.strip()
        if not vehicle_desc:
            raise ValidationException("תיאור רכב הוא שדה חובה", field="vehicle_description")
        if len(vehicle_desc) > 200:
            raise ValidationException(
                "תיאור רכב לא יכול לחרוג מ-200 תווים", field="vehicle_description"
            )

        is_safe, pattern = TextSanitizer.check_for_injection(vehicle_desc)
        if not is_safe:
            raise ValidationException("תיאור רכב מכיל תוכן לא תקין", field="vehicle_description")

        sanitized = TextSanitizer.sanitize(vehicle_desc, max_length=200)

        profile = await self._get_or_create_profile(user_id)
        profile.vehicle_description = sanitized
        await self.db.commit()

        logger.info(
            "תיאור רכב נשמר",
            extra_data={"user_id": user_id, "desc_length": len(sanitized)},
        )
        return sanitized

    async def save_dress_code(self, user_id: int, dress_code: str) -> tuple[str, bool]:
        """
        שמירת קוד לבוש וקביעה אם נדרש אימות.

        Args:
            user_id: מזהה המשתמש
            dress_code: ערך מ-DressCode enum

        Returns:
            tuple של (קוד לבוש, האם נדרש אימות)

        Raises:
            ValidationException: קוד לבוש לא תקין
        """
        valid_values = {e.value for e in DressCode}
        if dress_code not in valid_values:
            raise ValidationException(
                f"קוד לבוש לא תקין. ערכים אפשריים: {', '.join(valid_values)}",
                field="dress_code",
            )

        profile = await self._get_or_create_profile(user_id)
        profile.dress_code = dress_code

        # הפעלת תקופת ניסיון דרך SubscriptionService
        # (עם הגנה מפני הפעלה כפולה + קביעת subscription_status)
        subscription_service = DriverSubscriptionService(self.db)
        await subscription_service.activate_trial(user_id)

        needs_verification = self.requires_verification(dress_code)

        await self.db.commit()

        logger.info(
            "קוד לבוש נשמר",
            extra_data={
                "user_id": user_id,
                "dress_code": dress_code,
                "needs_verification": needs_verification,
            },
        )
        return dress_code, needs_verification

    @staticmethod
    def requires_verification(dress_code: str) -> bool:
        """בדיקה אם קוד הלבוש דורש אימות (זרם חרדי)"""
        return dress_code in HAREDI_DRESS_CODES

    # ==================== מתודות פנימיות ====================

    async def _get_user(self, user_id: int) -> User:
        """שליפת משתמש לפי ID"""
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundException("User", user_id)
        return user

    async def _get_or_create_profile(self, user_id: int) -> DriverProfile:
        """שליפת פרופיל נהג קיים או יצירת חדש (עם ערכי ברירת מחדל זמניים)"""
        result = await self.db.execute(
            select(DriverProfile).where(DriverProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if profile:
            return profile

        # יצירת פרופיל עם ערכי placeholder — יתמלאו בהמשך הרישום
        profile = DriverProfile(
            user_id=user_id,
            birth_date=date(2000, 1, 1),  # placeholder — יוחלף בשלב 2
            vehicle_description="",        # placeholder — יוחלף בשלב 3
            vehicle_category="car",        # placeholder — יוחלף בעתיד
            dress_code="secular",          # placeholder — יוחלף בשלב 4
        )
        self.db.add(profile)
        await self.db.commit()
        await self.db.refresh(profile)

        logger.info(
            "פרופיל נהג חדש נוצר",
            extra_data={"user_id": user_id, "profile_id": profile.id},
        )
        return profile

    @staticmethod
    def _parse_date(date_str: str) -> date:
        """פרסור תאריך בפורמט dd/mm/yyyy"""
        date_str = date_str.strip()
        try:
            return datetime.strptime(date_str, "%d/%m/%Y").date()
        except ValueError:
            raise ValidationException(
                "פורמט תאריך לא תקין. השתמש בפורמט dd/mm/yyyy (לדוגמה: 01/10/1977)",
                field="birth_date",
            )

    @staticmethod
    def _calculate_age(birth_date: date) -> int:
        """חישוב גיל מדויק"""
        today = date.today()
        return today.year - birth_date.year - (
            (today.month, today.day) < (birth_date.month, birth_date.day)
        )
