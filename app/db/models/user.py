"""
User Model - Senders and Couriers
"""
import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum as SQLEnum, Boolean

from app.db.database import Base


class UserRole(str, enum.Enum):
    SENDER = "sender"
    COURIER = "courier"
    ADMIN = "admin"


class User(Base):
    """User model for senders and couriers"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String(20), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=True)
    role = Column(SQLEnum(UserRole), default=UserRole.SENDER, nullable=False)
    is_active = Column(Boolean, default=True)
    platform = Column(String(20), nullable=False)  # whatsapp or telegram
    telegram_chat_id = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
