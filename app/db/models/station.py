"""
Station Model - תחנת משלוחים

תחנה היא ישות עסקית שמנהלת משלוחים.
לכל תחנה יש בעלים (station owner) וסדרנים (dispatchers) שמנהלים את המשלוחים.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from app.db.database import Base


class Station(Base):
    """מודל תחנת משלוחים"""

    __tablename__ = "stations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)

    # בעל התחנה
    owner_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)

    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # קשרים
    owner = relationship("User", foreign_keys=[owner_id])
    dispatchers = relationship("StationDispatcher", back_populates="station")
