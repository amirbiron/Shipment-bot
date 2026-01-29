"""
Outbox Message Model - Transactional Outbox Pattern
"""
import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum as SQLEnum, JSON, Boolean

from app.db.database import Base


class MessagePlatform(str, enum.Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"


class MessageStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


class OutboxMessage(Base):
    """Pending broadcasts with retry tracking for transactional outbox pattern"""

    __tablename__ = "outbox_messages"

    id = Column(Integer, primary_key=True, index=True)

    platform = Column(SQLEnum(MessagePlatform), nullable=False)
    recipient_id = Column(String(50), nullable=False)  # Phone or chat ID

    message_type = Column(String(50), nullable=False)  # e.g., "delivery_broadcast", "confirmation"
    message_content = Column(JSON, nullable=False)

    status = Column(SQLEnum(MessageStatus), default=MessageStatus.PENDING, index=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)

    # Error tracking
    last_error = Column(String(1000), nullable=True)
