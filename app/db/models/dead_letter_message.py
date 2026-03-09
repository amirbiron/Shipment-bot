"""
Dead Letter Message Model — הודעות שנכשלו סופית לאחר מיצוי כל ניסיונות ה-retry.

חלק ממנגנון retry חכם (פיצ'ר 3): הודעות שכשלו max_retries פעמים
נשמרות כאן לצפייה ו-retry ידני דרך פאנל הניהול.
"""
import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum as SQLEnum, JSON, Text

from app.db.database import Base


class DeadLetterStatus(str, enum.Enum):
    FAILED = "failed"         # נכשלה סופית — ממתינה לטיפול
    RETRIED = "retried"       # נשלחה מחדש ידנית
    DISCARDED = "discarded"   # נמחקה ידנית (לא רלוונטית)


class DeadLetterMessage(Base):
    """הודעות שנכשלו סופית ועברו ל-dead letter queue"""

    __tablename__ = "dead_letter_messages"

    id = Column(Integer, primary_key=True, index=True)

    # נתוני ההודעה המקורית
    original_message_id = Column(Integer, nullable=False, index=True)
    platform = Column(String(20), nullable=False)
    recipient_id = Column(String(100), nullable=False, index=True)
    message_type = Column(String(50), nullable=False)
    message_content = Column(JSON, nullable=False)

    # מידע על הכשלון
    retry_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    failure_reason = Column(String(200), nullable=True)  # סיווג: transient/permanent/unknown

    # סטטוס ב-dead letter queue
    status = Column(
        SQLEnum(DeadLetterStatus),
        default=DeadLetterStatus.FAILED,
        index=True,
    )

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    original_created_at = Column(DateTime, nullable=True)  # מתי נוצרה ההודעה המקורית
    retried_at = Column(DateTime, nullable=True)  # מתי נשלחה מחדש ידנית
