"""
Driver Session Model — ניהול סשן נהג (iDriver)

עוקב אחרי סשנים פעילים ולוגיקת ניתוק 24 שעות.
לכל נהג סשן אחד בלבד — כשפג תוקף, כל החיפושים מושהים אוטומטית.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, DateTime,
    Boolean, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


class DriverSession(Base):
    """סשן נהג — מעקב פעילות ולוגיקת 24 שעות"""

    __tablename__ = "driver_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True,
    )

    # מחזור חיים של הסשן
    session_start_at = Column(DateTime, default=datetime.utcnow)
    last_message_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True, index=True)

    # תזכורת ניתוק 24 שעות — אם לא NULL, נשלחה תזכורת "2 דקות לתום הסשן"
    reminder_sent_at = Column(DateTime, nullable=True)

    # חותמות זמן
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # קשרים
    user = relationship("User", foreign_keys=[user_id])
