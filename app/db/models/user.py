"""
User Model - Senders and Couriers
"""
import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum as SQLEnum, Boolean, Text

from app.db.database import Base


class UserRole(str, enum.Enum):
    SENDER = "sender"
    COURIER = "courier"
    ADMIN = "admin"


class ApprovalStatus(str, enum.Enum):
    """Courier approval status"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class User(Base):
    """User model for senders and couriers"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    # הערה: בחלק מהסביבות phone_number מוגדר כ-NOT NULL בפרודקשן,
    # לכן נשמור על עקביות גם במודל. עבור Telegram אנחנו שומרים placeholder (tg:...).
    phone_number = Column(String(20), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=True)
    full_name = Column(String(150), nullable=True)  # Legal name for couriers
    role = Column(SQLEnum(UserRole), default=UserRole.SENDER, nullable=False)
    is_active = Column(Boolean, default=True)
    platform = Column(String(20), nullable=False)  # whatsapp or telegram
    telegram_chat_id = Column(String(50), unique=True, nullable=True, index=True)

    # Courier-specific fields
    approval_status = Column(
        SQLEnum(
            ApprovalStatus,
            name='approval_status',
            create_type=False,
            values_callable=lambda x: [e.value for e in x]  # שולח 'pending' במקום 'PENDING'
        ),
        nullable=True,
        index=True
    )
    id_document_url = Column(Text, nullable=True)  # Path to ID/license photo
    service_area = Column(String(100), nullable=True)  # Geographic area
    terms_accepted_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def is_approved_courier(self) -> bool:
        """Check if user is an approved courier"""
        return self.role == UserRole.COURIER and self.approval_status == ApprovalStatus.APPROVED

    @property
    def is_pending_courier(self) -> bool:
        """Check if user is pending approval"""
        return self.role == UserRole.COURIER and self.approval_status == ApprovalStatus.PENDING
