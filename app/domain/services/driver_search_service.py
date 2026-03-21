"""
שירות חיפוש נסיעות (iDriver) — סשן 5

מנהל את החיפושים הפעילים של הנהג:
- יצירת חיפוש חדש (מסלול / אזורי / מיקום)
- שליפת חיפושים פעילים
- מחיקת חיפוש בודד או כל החיפושים
- אכיפת מגבלת 9 חיפושים פעילים
"""
from datetime import datetime
from html import escape
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update

from app.db.models.driver_search import (
    DriverSearch,
    DriverSearchStatus,
    MAX_ACTIVE_SEARCHES_PER_USER,
)
from app.schemas.driver import DriverSearchCreate
from app.core.logging import get_logger
from app.core.exceptions import ValidationException, NotFoundException

logger = get_logger(__name__)


class DriverSearchService:
    """שירות חיפוש נסיעות — CRUD על חיפושים פעילים של נהג"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_search(
        self,
        user_id: int,
        origin_city: str,
        destination_city: str,
        is_area_search: bool = False,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> DriverSearch:
        """
        יצירת חיפוש חדש.

        Args:
            user_id: מזהה המשתמש
            origin_city: עיר מוצא
            destination_city: עיר יעד
            is_area_search: האם חיפוש אזורי
            latitude: קו רוחב (חיפוש מיקום)
            longitude: קו אורך (חיפוש מיקום)

        Returns:
            החיפוש שנוצר

        Raises:
            ValidationException: חריגה ממגבלת חיפושים או ולידציה
        """
        # אכיפת מגבלת חיפושים פעילים
        active_count = await self.get_active_search_count(user_id)
        if active_count >= MAX_ACTIVE_SEARCHES_PER_USER:
            raise ValidationException(
                f"הגעת למקסימום {MAX_ACTIVE_SEARCHES_PER_USER} חיפושים פעילים. "
                "מחק חיפוש קיים כדי להוסיף חדש."
            )

        # בדיקת כפילות — אותו מוצא + יעד (+ קואורדינטות לחיפוש GPS)
        existing = await self._find_duplicate(
            user_id, origin_city, destination_city, is_area_search,
            latitude=latitude, longitude=longitude,
        )
        if existing:
            raise ValidationException(
                f"כבר קיים חיפוש פעיל ליעד {destination_city}"
                + (f" ממוצא {origin_city}" if origin_city else "")
            )

        # ולידציה דרך Pydantic
        validated = DriverSearchCreate(
            origin_city=origin_city,
            destination_city=destination_city,
            is_area_search=is_area_search,
            latitude=latitude,
            longitude=longitude,
        )

        search = DriverSearch(
            user_id=user_id,
            origin_city=validated.origin_city,
            destination_city=validated.destination_city,
            is_area_search=validated.is_area_search,
            latitude=Decimal(str(validated.latitude)) if validated.latitude is not None else None,
            longitude=Decimal(str(validated.longitude)) if validated.longitude is not None else None,
            status=DriverSearchStatus.ACTIVE.value,
        )
        self.db.add(search)
        await self.db.commit()
        await self.db.refresh(search)

        logger.info(
            "חיפוש חדש נוצר",
            extra_data={
                "user_id": user_id,
                "search_id": search.id,
                "origin": origin_city,
                "destination": destination_city,
                "is_area": is_area_search,
            },
        )
        return search

    async def create_location_search(
        self,
        user_id: int,
        latitude: float,
        longitude: float,
    ) -> DriverSearch:
        """
        יצירת חיפוש לפי מיקום GPS.

        Args:
            user_id: מזהה המשתמש
            latitude: קו רוחב
            longitude: קו אורך

        Returns:
            החיפוש שנוצר

        Raises:
            ValidationException: חריגה ממגבלת חיפושים
        """
        return await self.create_search(
            user_id=user_id,
            origin_city="מיקום נוכחי",
            destination_city="אזור מיקום",
            is_area_search=True,
            latitude=latitude,
            longitude=longitude,
        )

    async def get_active_searches(self, user_id: int) -> list[DriverSearch]:
        """
        שליפת כל החיפושים הפעילים של נהג.

        Args:
            user_id: מזהה המשתמש

        Returns:
            רשימת חיפושים פעילים
        """
        result = await self.db.execute(
            select(DriverSearch)
            .where(
                DriverSearch.user_id == user_id,
                DriverSearch.status == DriverSearchStatus.ACTIVE.value,
            )
            .order_by(DriverSearch.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_active_search_count(self, user_id: int) -> int:
        """
        ספירת חיפושים פעילים של נהג.

        Args:
            user_id: מזהה המשתמש

        Returns:
            מספר חיפושים פעילים
        """
        result = await self.db.execute(
            select(func.count(DriverSearch.id))
            .where(
                DriverSearch.user_id == user_id,
                DriverSearch.status == DriverSearchStatus.ACTIVE.value,
            )
        )
        return result.scalar_one()

    async def delete_search(self, user_id: int, search_id: int) -> bool:
        """
        מחיקת חיפוש בודד (soft-delete).

        Args:
            user_id: מזהה המשתמש (לבדיקת בעלות)
            search_id: מזהה החיפוש

        Returns:
            True אם נמחק בהצלחה

        Raises:
            NotFoundException: חיפוש לא נמצא
            ValidationException: החיפוש לא שייך למשתמש
        """
        result = await self.db.execute(
            select(DriverSearch).where(DriverSearch.id == search_id)
        )
        search = result.scalar_one_or_none()

        if not search:
            raise NotFoundException("DriverSearch", search_id)

        # בדיקת בעלות
        if search.user_id != user_id:
            raise ValidationException("אין הרשאה למחוק חיפוש זה")

        if search.status == DriverSearchStatus.DELETED.value:
            raise ValidationException("החיפוש כבר מחוק")

        search.status = DriverSearchStatus.DELETED.value
        search.updated_at = datetime.utcnow()
        await self.db.commit()

        logger.info(
            "חיפוש נמחק",
            extra_data={
                "user_id": user_id,
                "search_id": search_id,
                "destination": search.destination_city,
            },
        )
        return True

    async def pause_all_searches(self, user_id: int) -> int:
        """
        השהיית כל החיפושים הפעילים (ACTIVE → PAUSED).

        Args:
            user_id: מזהה המשתמש

        Returns:
            מספר חיפושים שהושהו

        Raises:
            ValidationException: משתמש לא נמצא או לא מורשה
        """
        await self._verify_driver_ownership(user_id)

        result = await self.db.execute(
            update(DriverSearch)
            .where(
                DriverSearch.user_id == user_id,
                DriverSearch.status == DriverSearchStatus.ACTIVE.value,
            )
            .values(
                status=DriverSearchStatus.PAUSED.value,
                updated_at=datetime.utcnow(),
            )
        )
        await self.db.commit()

        count = result.rowcount
        if count > 0:
            logger.info(
                "כל החיפושים הושהו",
                extra_data={"user_id": user_id, "count": count},
            )
        return count

    async def resume_all_searches(self, user_id: int) -> int:
        """
        חידוש כל החיפושים המושהים (PAUSED → ACTIVE).

        מכבד את מגבלת MAX_ACTIVE_SEARCHES_PER_USER — אם כבר יש חיפושים
        פעילים, מחדש רק עד למכסה המותרת.

        Args:
            user_id: מזהה המשתמש

        Returns:
            מספר חיפושים שחודשו

        Raises:
            ValidationException: אם כבר הגיע למקסימום חיפושים פעילים או משתמש לא מורשה
        """
        await self._verify_driver_ownership(user_id)
        # בדיקה כמה חיפושים פעילים כבר קיימים
        active_count_result = await self.db.execute(
            select(func.count())
            .select_from(DriverSearch)
            .where(
                DriverSearch.user_id == user_id,
                DriverSearch.status == DriverSearchStatus.ACTIVE.value,
            )
        )
        current_active = active_count_result.scalar() or 0

        if current_active >= MAX_ACTIVE_SEARCHES_PER_USER:
            raise ValidationException(
                f"כבר יש {current_active} חיפושים פעילים — "
                f"המקסימום הוא {MAX_ACTIVE_SEARCHES_PER_USER}."
            )

        slots_available = MAX_ACTIVE_SEARCHES_PER_USER - current_active

        # שליפת חיפושים מושהים לפי סדר יצירה (ישנים קודם)
        paused_result = await self.db.execute(
            select(DriverSearch.id)
            .where(
                DriverSearch.user_id == user_id,
                DriverSearch.status == DriverSearchStatus.PAUSED.value,
            )
            .order_by(DriverSearch.created_at.asc())
            .limit(slots_available)
        )
        ids_to_resume = [row[0] for row in paused_result.all()]

        if not ids_to_resume:
            return 0

        await self.db.execute(
            update(DriverSearch)
            .where(DriverSearch.id.in_(ids_to_resume))
            .values(
                status=DriverSearchStatus.ACTIVE.value,
                updated_at=datetime.utcnow(),
            )
        )
        await self.db.commit()

        count = len(ids_to_resume)
        logger.info(
            "חיפושים חודשו",
            extra_data={"user_id": user_id, "count": count},
        )

        # שליפת מספר מושהים שנותרו (אם לא כולם חודשו)
        remaining_result = await self.db.execute(
            select(func.count())
            .select_from(DriverSearch)
            .where(
                DriverSearch.user_id == user_id,
                DriverSearch.status == DriverSearchStatus.PAUSED.value,
            )
        )
        remaining_paused = remaining_result.scalar() or 0

        if remaining_paused > 0:
            logger.info(
                "נותרו חיפושים מושהים — חריגה ממגבלת מקסימום",
                extra_data={
                    "user_id": user_id,
                    "remaining_paused": remaining_paused,
                },
            )

        return count

    async def get_non_deleted_searches(self, user_id: int) -> list[DriverSearch]:
        """
        שליפת כל החיפושים שלא נמחקו (פעילים + מושהים).

        Args:
            user_id: מזהה המשתמש

        Returns:
            רשימת חיפושים פעילים ומושהים
        """
        result = await self.db.execute(
            select(DriverSearch)
            .where(
                DriverSearch.user_id == user_id,
                DriverSearch.status.in_([
                    DriverSearchStatus.ACTIVE.value,
                    DriverSearchStatus.PAUSED.value,
                ]),
            )
            .order_by(DriverSearch.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete_all_searches(self, user_id: int) -> int:
        """
        מחיקת כל החיפושים שלא נמחקו — פעילים ומושהים (soft-delete).

        Args:
            user_id: מזהה המשתמש

        Returns:
            מספר חיפושים שנמחקו
        """
        result = await self.db.execute(
            update(DriverSearch)
            .where(
                DriverSearch.user_id == user_id,
                DriverSearch.status.in_([
                    DriverSearchStatus.ACTIVE.value,
                    DriverSearchStatus.PAUSED.value,
                ]),
            )
            .values(
                status=DriverSearchStatus.DELETED.value,
                updated_at=datetime.utcnow(),
            )
        )
        await self.db.commit()

        count = result.rowcount
        if count > 0:
            logger.info(
                "כל החיפושים נמחקו",
                extra_data={"user_id": user_id, "count": count},
            )
        return count

    async def _verify_driver_ownership(self, user_id: int) -> None:
        """אימות שהמשתמש קיים ושייך לתפקיד נהג — בדיקת authorization"""
        from app.db.models.user import User, UserRole

        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise ValidationException("משתמש לא נמצא")
        if user.role != UserRole.DRIVER:
            raise ValidationException("אין הרשאה לבצע פעולה זו — נדרש תפקיד נהג")

    async def _find_duplicate(
        self,
        user_id: int,
        origin_city: str,
        destination_city: str,
        is_area_search: bool,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> DriverSearch | None:
        """בדיקת כפילות חיפוש פעיל — כולל קואורדינטות לחיפושי GPS"""
        conditions = [
            DriverSearch.user_id == user_id,
            DriverSearch.origin_city == origin_city,
            DriverSearch.destination_city == destination_city,
            DriverSearch.is_area_search == is_area_search,
            DriverSearch.status == DriverSearchStatus.ACTIVE.value,
        ]
        # חיפושי GPS — כפילות רק אם אותן קואורדינטות.
        # הסכמה (DriverSearchCreate) מבטיחה ששתי הקואורדינטות מגיעות יחד או
        # שתיהן None, כך שאין צורך לטפל בקואורדינטה חלקית.
        if latitude is not None and longitude is not None:
            conditions.append(DriverSearch.latitude == Decimal(str(latitude)))
            conditions.append(DriverSearch.longitude == Decimal(str(longitude)))
        else:
            conditions.append(DriverSearch.latitude.is_(None))
            conditions.append(DriverSearch.longitude.is_(None))

        result = await self.db.execute(
            select(DriverSearch).where(*conditions)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def format_search_summary(
        search: DriverSearch, *, html_escape: bool = True
    ) -> str:
        """
        פורמט חיפוש בודד לתצוגה.

        Args:
            search: אובייקט חיפוש
            html_escape: האם להחיל HTML escape (ברירת מחדל True).
                         יש להעביר False כשהטקסט מוצג כ-plain text (למשל כפתורי מקלדת).

        Returns:
            טקסט מפורמט
        """
        esc = escape if html_escape else lambda s: s
        area_marker = " (אזורי)" if search.is_area_search else ""
        if search.origin_city and search.origin_city != "מיקום נוכחי":
            return (
                f"📍 {esc(search.destination_city)} ← "
                f"{esc(search.origin_city)}{esc(area_marker)}"
            )
        if search.latitude is not None and search.longitude is not None:
            return f"📍 {esc(search.destination_city)} ← מיקום GPS{esc(area_marker)}"
        return f"📍 {esc(search.destination_city)}{esc(area_marker)}"

    @staticmethod
    def format_searches_list(searches: list[DriverSearch]) -> str:
        """
        פורמט רשימת חיפושים לתצוגה.

        Args:
            searches: רשימת חיפושים

        Returns:
            טקסט מפורמט
        """
        if not searches:
            return "אין חיפושים פעילים כרגע."

        lines = []
        for i, search in enumerate(searches, 1):
            lines.append(
                f"{i}. {DriverSearchService.format_search_summary(search)}"
            )
        return "\n".join(lines)
