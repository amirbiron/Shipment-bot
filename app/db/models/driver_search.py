"""
Driver Search Model — חיפושים פעילים של נהג (iDriver)

כל רשומה מייצגת חיפוש פעיל (עד 9 לכל משתמש).
תומך בחיפוש לפי מסלול (עיר מוצא → עיר יעד) או לפי אזור (רדיוס ממיקום).
"""
import enum
from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, BigInteger, String, DateTime,
    Boolean, Numeric, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


class DriverSearchStatus(str, enum.Enum):
    """סטטוס חיפוש"""
    ACTIVE = "active"    # חיפוש פעיל
    PAUSED = "paused"    # מושהה
    DELETED = "deleted"  # מחוק (soft-delete)


# מגבלת חיפושים פעילים למשתמש — נאכף בשכבת האפליקציה
MAX_ACTIVE_SEARCHES_PER_USER = 9


class DriverSearch(Base):
    """חיפוש פעיל של נהג — מסלול או אזור"""

    __tablename__ = "driver_searches"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # מיקום מוצא ויעד
    origin_city = Column(String(100), nullable=False)
    destination_city = Column(String(100), nullable=False)

    # חיפוש לפי אזור (רדיוס)
    is_area_search = Column(Boolean, default=False)

    # קואורדינטות — רלוונטי רק כאשר is_area_search=True
    latitude = Column(Numeric(10, 7), nullable=True)
    longitude = Column(Numeric(10, 7), nullable=True)

    # סטטוס חיפוש — soft-delete דרך "deleted"
    status = Column(String(50), default=DriverSearchStatus.ACTIVE.value, index=True)

    # חותמות זמן
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # קשרים
    user = relationship("User", foreign_keys=[user_id])
