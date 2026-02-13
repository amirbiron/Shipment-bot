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
    owner_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)

    is_active = Column(Boolean, default=True, index=True)

    # שלב 4: קבוצות תחנה לשידור וכרטיסים סגורים
    public_group_chat_id = Column(String(100), nullable=True)  # קבוצה ציבורית לשידור משלוחים
    private_group_chat_id = Column(String(100), nullable=True)  # קבוצה פרטית לכרטיסים סגורים
    public_group_platform = Column(String(20), nullable=True)  # "telegram" / "whatsapp"
    private_group_platform = Column(String(20), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # קשרים
    owner = relationship("User", foreign_keys=[owner_id])
    dispatchers = relationship("StationDispatcher", back_populates="station")
    owners = relationship("StationOwner", back_populates="station")
