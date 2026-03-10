"""
Audit Log Model — לוג ביקורת לפעולות רגישות במערכת

רישום בלתי-הפיך של כל פעולה רגישה: "מי שינה מה מ-X ל-Y".
מכסה פעולות מנהלתיות בתחנה, שינויי הרשאות, פעולות ארנק ושינויי סטטוס.
"""
import enum
from datetime import datetime
from sqlalchemy import BigInteger, Column, DateTime, Enum as SQLEnum, ForeignKey, Index, Integer, String
from sqlalchemy.types import JSON

from app.db.database import Base


class AuditActionType(str, enum.Enum):
    """סוגי פעולות הנרשמות בלוג ביקורת"""

    # פעולות מנהלתיות בתחנה (קיימים)
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

    # שינויי הרשאות שליחים
    COURIER_APPROVED = "courier_approved"
    COURIER_REJECTED = "courier_rejected"
    COURIER_BLOCKED = "courier_blocked"

    # שינויי סטטוס משלוח
    DELIVERY_STATUS_CHANGED = "delivery_status_changed"

    # פעולות ארנק
    WALLET_DEBIT = "wallet_debit"
    WALLET_CREDIT = "wallet_credit"


class AuditLog(Base):
    """לוג ביקורת — רישום בלתי-הפיך של פעולות רגישות"""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    # station_id אופציונלי — פעולות כמו אישור שליח לא קשורות לתחנה ספציפית
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=True, index=True)
    actor_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(SQLEnum(AuditActionType), nullable=False, index=True)
    # מזהה המשתמש שהפעולה בוצעה עליו (בעלים/סדרן/נהג שנוסף או הוסר)
    target_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)

    # סוג הישות שהפעולה בוצעה עליה (delivery, wallet, user, station)
    entity_type = Column(String(50), nullable=True, index=True)
    # מזהה הישות (delivery_id, wallet_id, user_id, station_id)
    # BigInteger כי עשוי לאחסן Telegram user IDs שחורגים מטווח int32
    entity_id = Column(BigInteger, nullable=True)

    # ערכים לפני ואחרי השינוי — לשחזור ומעקב
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)

    # פרטי השינוי בפורמט JSON — "מה שונה מ-X ל-Y" (תואמות לאחור)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        # אינדקסים מורכבים לשאילתות סינון בפאנל
        Index("ix_audit_logs_station_action_created", "station_id", "action", created_at.desc()),
        Index("ix_audit_logs_station_actor_created", "station_id", "actor_user_id", created_at.desc()),
        Index("ix_audit_logs_target_user", "target_user_id", created_at.desc()),
        # אינדקס מורכב לחיפוש לפי ישות
        Index("ix_audit_logs_entity", "entity_type", "entity_id", created_at.desc()),
    )
