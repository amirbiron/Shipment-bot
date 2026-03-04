"""
שירות ניהול סשנים לנהג (iDriver) — סשן 6

מנהל את מחזור החיים של סשן הנהג:
- יצירה / חידוש סשן
- עדכון פעילות אחרונה (touch)
- בדיקת סשנים שפג תוקפם (24 שעות)
- שליחת תזכורת 2 דקות לפני ניתוק
- ניתוק אוטומטי והשהיית חיפושים
"""
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.driver_session import DriverSession
from app.db.models.driver_search import DriverSearch, DriverSearchStatus
from app.core.logging import get_logger

logger = get_logger(__name__)

# קבועי זמן — 24 שעות עם תזכורת 2 דקות לפני
SESSION_DURATION_HOURS = 24
REMINDER_MINUTES_BEFORE = 2
_REMINDER_THRESHOLD = timedelta(
    hours=SESSION_DURATION_HOURS,
    minutes=-REMINDER_MINUTES_BEFORE,
)  # 23 שעות 58 דקות
_EXPIRY_THRESHOLD = timedelta(hours=SESSION_DURATION_HOURS)


class DriverSessionService:
    """שירות ניהול סשנים — מעקב פעילות ולוגיקת 24 שעות"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def start_session(self, user_id: int) -> DriverSession:
        """
        יצירת סשן חדש או חידוש סשן קיים.

        אם כבר קיים סשן — מאפס את הטיימר.
        אם לא — יוצר סשן חדש.

        Args:
            user_id: מזהה המשתמש

        Returns:
            הסשן שנוצר/חודש
        """
        session = await self._get_session(user_id)
        now = datetime.utcnow()

        if session:
            session.session_start_at = now
            session.last_message_at = now
            session.is_active = True
            session.reminder_sent_at = None
            session.updated_at = now
            await self.db.commit()
            await self.db.refresh(session)
            logger.info(
                "סשן נהג חודש",
                extra_data={"user_id": user_id, "session_id": session.id},
            )
            return session

        session = DriverSession(
            user_id=user_id,
            session_start_at=now,
            last_message_at=now,
            is_active=True,
            reminder_sent_at=None,
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        logger.info(
            "סשן נהג חדש נוצר",
            extra_data={"user_id": user_id, "session_id": session.id},
        )
        return session

    async def touch_session(self, user_id: int) -> DriverSession | None:
        """
        עדכון פעילות אחרונה — נקרא בכל הודעה מנהג.

        אם אין סשן פעיל — יוצר סשן חדש.

        Args:
            user_id: מזהה המשתמש

        Returns:
            הסשן המעודכן, או None אם אין סשן
        """
        session = await self._get_session(user_id)
        now = datetime.utcnow()

        if not session:
            # יצירת סשן חדש אוטומטית אם אין
            return await self.start_session(user_id)

        if not session.is_active:
            # סשן לא פעיל — חידוש אוטומטי
            return await self.start_session(user_id)

        session.last_message_at = now
        session.updated_at = now
        # איפוס תזכורת — פעילות חדשה מאריכה את הסשן,
        # כך שתזכורת חדשה תישלח בהתאם לזמן העדכני
        if session.reminder_sent_at is not None:
            session.reminder_sent_at = None
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def is_session_active(self, user_id: int) -> bool:
        """
        בדיקה אם לנהג יש סשן פעיל.

        Args:
            user_id: מזהה המשתמש

        Returns:
            True אם הסשן פעיל
        """
        session = await self._get_session(user_id)
        if not session:
            return False
        return session.is_active

    async def get_expiring_sessions(self) -> list[DriverSession]:
        """
        שליפת סשנים שעומדים לפוג — בטווח 23:58-24:00 שעות מאז הפעילות האחרונה.

        מחזיר רק סשנים שעדיין לא נשלחה להם תזכורת.

        Returns:
            רשימת סשנים שצריכים תזכורת
        """
        now = datetime.utcnow()
        reminder_cutoff = now - _REMINDER_THRESHOLD  # 23:58 לפני
        expiry_cutoff = now - _EXPIRY_THRESHOLD       # 24:00 לפני

        result = await self.db.execute(
            select(DriverSession).where(
                DriverSession.is_active == True,  # noqa: E712
                DriverSession.reminder_sent_at.is_(None),
                DriverSession.last_message_at <= reminder_cutoff,
                DriverSession.last_message_at > expiry_cutoff,
            )
        )
        return list(result.scalars().all())

    async def mark_reminder_sent(self, session_id: int) -> None:
        """
        סימון תזכורת כנשלחה — מונע שליחה כפולה.

        Args:
            session_id: מזהה הסשן
        """
        now = datetime.utcnow()
        await self.db.execute(
            update(DriverSession)
            .where(DriverSession.id == session_id)
            .values(reminder_sent_at=now, updated_at=now)
        )
        await self.db.commit()

    async def get_expired_sessions(self) -> list[DriverSession]:
        """
        שליפת סשנים שפג תוקפם — מעל 24 שעות מאז הפעילות האחרונה.

        Returns:
            רשימת סשנים שצריכים ניתוק
        """
        now = datetime.utcnow()
        expiry_cutoff = now - _EXPIRY_THRESHOLD

        result = await self.db.execute(
            select(DriverSession).where(
                DriverSession.is_active == True,  # noqa: E712
                DriverSession.last_message_at <= expiry_cutoff,
            )
        )
        return list(result.scalars().all())

    async def disconnect_session(self, user_id: int) -> int:
        """
        ניתוק סשן — סימון כלא פעיל והשהיית כל החיפושים הפעילים.

        Args:
            user_id: מזהה המשתמש

        Returns:
            מספר חיפושים שהושהו
        """
        session = await self._get_session(user_id)
        if not session or not session.is_active:
            return 0

        now = datetime.utcnow()
        session.is_active = False
        session.updated_at = now

        # השהיית כל החיפושים הפעילים
        result = await self.db.execute(
            update(DriverSearch)
            .where(
                DriverSearch.user_id == user_id,
                DriverSearch.status == DriverSearchStatus.ACTIVE.value,
            )
            .values(
                status=DriverSearchStatus.PAUSED.value,
                updated_at=now,
            )
        )
        paused_count = result.rowcount

        await self.db.commit()

        logger.info(
            "סשן נהג נותק — חיפושים הושהו",
            extra_data={
                "user_id": user_id,
                "paused_searches": paused_count,
            },
        )
        return paused_count

    async def _get_session(self, user_id: int) -> DriverSession | None:
        """שליפת סשן לפי user_id"""
        result = await self.db.execute(
            select(DriverSession).where(DriverSession.user_id == user_id)
        )
        return result.scalar_one_or_none()
