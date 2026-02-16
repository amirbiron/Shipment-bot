"""
Audit Log Model — לוג ביקורת לפעולות מנהלתיות בתחנה

רישום בלתי-הפיך של כל פעולה מנהלתית: "מי שינה מה מ-X ל-Y".
חיוני לתחנות עם מספר בעלים.
"""
import enum
from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, ForeignKey, String, Enum as SQLEnum, Text
from sqlalchemy.types import JSON

from app.db.database import Base


class AuditActionType(str, enum.Enum):
    """סוגי פעולות מנהלתיות הנרשמות בלוג ביקורת"""
    OWNER_ADDED = "owner_added"
    OWNER_REMOVED = "owner_removed"
    DISPATCHER_ADDED = "dispatcher_added"
    DISPATCHER_REMOVED = "dispatcher_removed"
    BLACKLIST_ADDED = "blacklist_added"
    BLACKLIST_REMOVED = "blacklist_removed"
    COMMISSION_RATE_UPDATED = "commission_rate_updated"
    STATION_SETTINGS_UPDATED = "station_settings_updated"
    GROUP_SETTINGS_UPDATED = "group_settings_updated"
    AUTO_BLOCK_SETTINGS_UPDATED = "auto_block_settings_updated"
    MANUAL_CHARGE_CREATED = "manual_charge_created"


class AuditLog(Base):
    """לוג ביקורת — רישום בלתי-הפיך של פעולות מנהלתיות"""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(SQLEnum(AuditActionType), nullable=False, index=True)
    # מזהה המשתמש שהפעולה בוצעה עליו (בעלים/סדרן/נהג שנוסף או הוסר)
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # פרטי השינוי בפורמט JSON — "מה שונה מ-X ל-Y"
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
