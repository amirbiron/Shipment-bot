"""
Conversation Session Model - State Machine Tracking
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON

from app.db.database import Base


class ConversationSession(Base):
    """Per-user state machine tracking for conversation flows"""

    __tablename__ = "conversation_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    platform = Column(String(20), nullable=False)  # whatsapp or telegram

    # State machine
    current_state = Column(String(100), nullable=False, default="INITIAL")

    # Context data for the current flow (pickup/dropoff collection etc.)
    context_data = Column(JSON, default=dict)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_activity_at = Column(DateTime, default=datetime.utcnow)
