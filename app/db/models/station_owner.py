"""
Station Owner Model - קישור בעלים לתחנה

בעל תחנה הוא משתמש עם הרשאות ניהול מלאות בתחנה ספציפית.
תומך בכמה בעלים לתחנה אחת (many-to-many).
"""
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.database import Base


class StationOwner(Base):
    """קישור בעלים לתחנה - many-to-many"""

    __tablename__ = "station_owners"

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # קשרים
    station = relationship("Station", back_populates="owners")
    user = relationship("User")

    # מניעת כפילות - בעלים יכול להיות משויך לתחנה פעם אחת
    __table_args__ = (
        UniqueConstraint('station_id', 'user_id', name='uq_station_owner'),
    )
