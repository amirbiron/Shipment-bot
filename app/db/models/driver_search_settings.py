"""
Driver Search Settings Model — הגדרות חיפוש נהג (iDriver)

שומר את העדפות הסינון של הנהג: סוג רכב, סוג נסיעה,
תצוגת משלוחים, מסגרת זמן ומצב "עתידי בלבד".
"""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, DateTime,
    Boolean, Time, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


class TripTypeFilter(str, enum.Enum):
    """סוגי נסיעה לסינון"""
    SHORT_DISTANCE = "short_distance"    # עד 15 ק"מ
    MEDIUM_DISTANCE = "medium_distance"  # 15-50 ק"מ
    LONG_DISTANCE = "long_distance"      # 50+ ק"מ (בין-עירוני)
    ANY_DISTANCE = "any_distance"        # כל מרחק
    RIDES = "rides"                      # נוסעים (אנשים, לא משלוחים)


class UpcomingTimeframe(str, enum.Enum):
    """מסגרת זמן לחיפוש נסיעות קרובות"""
    ONE_HOUR = "1_hour"        # שעה אחת
    TWO_HOURS = "2_hours"      # שתי שעות
    FIVE_HOURS = "5_hours"     # 5 שעות
    ALL = "all"                # הכל


class DriverSearchSettings(Base):
    """הגדרות חיפוש נהג — העדפות סינון אישיות"""

    __tablename__ = "driver_search_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True,
    )

    # סינון לפי סוג רכב
    vehicle_type_filter = Column(String(50), default="7_seater")

    # סינון לפי סוג נסיעה
    trip_type_filter = Column(String(50), default=TripTypeFilter.ANY_DISTANCE.value)

    # הצגת משלוחים בתוצאות חיפוש
    show_deliveries = Column(Boolean, default=True)

    # מסגרת זמן לנסיעות קרובות
    upcoming_timeframe = Column(String(50), default=UpcomingTimeframe.ALL.value)

    # מצב "עתידי בלבד" — רק נסיעות שמתחילות אחרי שעה מסוימת
    # חוק עסקי: future_only_enabled=True רק אם upcoming_timeframe="all"
    future_only_enabled = Column(Boolean, default=False)
    future_only_start_time = Column(Time, nullable=True)  # פורמט HH:MM

    # חותמות זמן
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # קשרים
    user = relationship("User", foreign_keys=[user_id])
