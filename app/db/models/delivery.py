"""
Delivery Model - Shipment Records
"""
import enum
import secrets
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum as SQLEnum, Float, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.db.database import Base


def generate_secure_token():
    """Generate a secure URL-safe token for delivery smart links"""
    return secrets.token_urlsafe(16)


class DeliveryStatus(str, enum.Enum):
    OPEN = "open"
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
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)

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
    courier_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    fee = Column(Float, default=10.0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    captured_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    sender = relationship("User", foreign_keys=[sender_id])
    courier = relationship("User", foreign_keys=[courier_id])
