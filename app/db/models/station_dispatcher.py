"""
Station Dispatcher Model - קישור סדרן לתחנה

סדרן הוא נהג מאושר שקיבל הרשאות ניהול ברמת תחנה ספציפית.
הוא רואה את כל תפריט הנהג + תפריט סדרן ייעודי.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.database import Base


class StationDispatcher(Base):
    """קישור סדרן לתחנה - many-to-many"""

    __tablename__ = "station_dispatchers"

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # קשרים
    station = relationship("Station", back_populates="dispatchers")
    user = relationship("User")

    # מניעת כפילות - סדרן יכול להיות משויך לתחנה פעם אחת
    __table_args__ = (
        UniqueConstraint('station_id', 'user_id', name='uq_station_dispatcher'),
    )
