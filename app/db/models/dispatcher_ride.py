"""
Dispatcher Ride Model — נסיעות שמפרסם סדרן (סשן 9)

נסיעה שסדרן מפרסם דרך מערכת ההפצה ומופיעה בתוצאות חיפוש נהגי iDriver.
"""
import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    DateTime,
    Numeric,
    ForeignKey,
    Text,
    Boolean,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


class DispatcherRideStatus(str, enum.Enum):
    """סטטוס נסיעה שפורסמה ע"י סדרן"""

    OPEN = "open"  # פתוחה — מופיעה בתוצאות חיפוש
    TAKEN = "taken"  # נתפסה ע"י נהג
    CANCELLED = "cancelled"  # בוטלה
    EXPIRED = "expired"  # פגה תוקף


# סטטוסים שנחשבים "פעילים" (מופיעים בחיפוש ובתצוגת סדרן)
ACTIVE_RIDE_STATUSES: tuple[DispatcherRideStatus, ...] = (
    DispatcherRideStatus.OPEN,
    DispatcherRideStatus.TAKEN,
)


class DispatcherRide(Base):
    """נסיעה שפורסמה ע"י סדרן — מופיעה בתוצאות חיפוש נהגים"""

    __tablename__ = "dispatcher_rides"

    id = Column(Integer, primary_key=True, index=True)

    # סדרן שפרסם את הנסיעה
    dispatcher_id = Column(
        BigInteger, ForeignKey("users.id"), nullable=False, index=True
    )
    # תחנה שהנסיעה שייכת אליה
    station_id = Column(
        Integer, ForeignKey("stations.id"), nullable=False, index=True
    )

    # מוצא ויעד
    origin_city = Column(String(100), nullable=False)
    destination_city = Column(String(100), nullable=False)

    # פרטי נסיעה
    seats = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    description = Column(Text, nullable=True)

    # סינון
    is_delivery = Column(Boolean, default=False, nullable=False)

    # סטטוס
    status = Column(
        String(50), default=DispatcherRideStatus.OPEN.value, nullable=False, index=True
    )

    # נהג שתפס את הנסיעה
    taken_by_user_id = Column(
        BigInteger, ForeignKey("users.id"), nullable=True, index=True
    )
    taken_at = Column(DateTime, nullable=True)

    # חותמות זמן
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # יחסים
    dispatcher = relationship("User", foreign_keys=[dispatcher_id])
    station = relationship("Station", foreign_keys=[station_id])
    taken_by = relationship("User", foreign_keys=[taken_by_user_id])
