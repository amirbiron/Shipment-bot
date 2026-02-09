"""
Webhook Event Model - טבלת idempotency למניעת עיבוד כפול של הודעות webhook.

כל הודעה נכנסת נרשמת לפי message_id. רק הודעות עם status=completed
נחסמות מ-retry. הודעות שנכשלו (processing ישן / failed) מאפשרות retry.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Index

from app.db.database import Base


class WebhookEvent(Base):
    """רשומת idempotency - הודעה שהתקבלה מ-webhook"""

    __tablename__ = "webhook_events"

    message_id = Column(String(200), primary_key=True)
    platform = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="processing")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_webhook_events_status_created", "status", "created_at"),
    )
