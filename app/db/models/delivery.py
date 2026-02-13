"""
Delivery Model - Shipment Records
"""
import enum
import secrets
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Enum as SQLEnum, Numeric, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.db.database import Base


def generate_secure_token():
    """Generate a secure URL-safe token for delivery smart links"""
    return secrets.token_urlsafe(16)


class DeliveryStatus(str, enum.Enum):
    OPEN = "open"
    PENDING_APPROVAL = "pending_approval"  # שלב 4: ממתין לאישור סדרן
    CAPTURED = "captured"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Delivery(Base):
    """Delivery/Shipment record"""

    __tablename__ = "deliveries"

    id = Column(Integer, primary_key=True, index=True)
    # Secure token for smart links (prevents ID guessing attacks)
    token = Column(String(32), unique=True, nullable=False, default=generate_secure_token, index=True)

    # Sender info
    sender_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)

    # Pickup details
    pickup_address = Column(String(500), nullable=False)
    pickup_contact_name = Column(String(100), nullable=True)
    pickup_contact_phone = Column(String(20), nullable=True)
    pickup_notes = Column(Text, nullable=True)

    # Dropoff details
    dropoff_address = Column(String(500), nullable=False)
    dropoff_contact_name = Column(String(100), nullable=True)
    dropoff_contact_phone = Column(String(20), nullable=True)
    dropoff_notes = Column(Text, nullable=True)

    # Delivery info
    status = Column(SQLEnum(DeliveryStatus), default=DeliveryStatus.OPEN, index=True)
    courier_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    fee = Column(Numeric(10, 2), default=10.0)

    # תחנה שיצרה את המשלוח (nullable - משלוחים ישירים לא שייכים לתחנה)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=True, index=True)

    # שלב 4: שדות זרימת אישור משלוח
    requesting_courier_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)  # שליח שביקש את המשלוח
    requested_at = Column(DateTime, nullable=True)  # מתי הוגשה הבקשה
    approved_by_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)  # סדרן שאישר/דחה
    approved_at = Column(DateTime, nullable=True)  # מתי התקבלה ההחלטה
    approval_decision = Column(String(20), nullable=True)  # "approved" / "rejected"

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    captured_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    sender = relationship("User", foreign_keys=[sender_id])
    courier = relationship("User", foreign_keys=[courier_id])
    requesting_courier = relationship("User", foreign_keys=[requesting_courier_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
