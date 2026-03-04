"""
Driver Profile Model — פרופיל נהג (iDriver)

פרופיל אישי של נהג כולל: פרטים אישיים, רכב, קוד לבוש,
סטטוס אימות וסטטוס מנוי.
"""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Date, DateTime,
    Text, Boolean, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


class VehicleCategory(str, enum.Enum):
    """קטגוריות רכב של נהג"""
    CAR = "car"                    # רכב סטנדרטי
    FOUR_SEATER = "4_seater"       # 4 מקומות (תא נוסעים)
    SEVEN_SEATER = "7_seater"      # 7 מקומות (מיניוואן / טנדר)
    EIGHT_PLUS = "8_plus"          # 8+ מקומות (אוטובוס)
    MOTORCYCLE = "motorcycle"      # אופנוע (משלוחים קלים)
    TRUCK = "truck"                # משאית
    VAN = "van"                    # וואן / פורגון


class DressCode(str, enum.Enum):
    """קוד לבוש של נהג"""
    HASSIDIC = "hassidic"                  # חסידי (כובע שחור, חליפה)
    ULTRA_ORTHODOX = "ultra_orthodox"       # חרדי
    MODERN_ORTHODOX = "modern_orthodox"     # חרדי מודרני
    RELIGIOUS_ELEGANT = "religious_elegant" # דתי אלגנט
    MIXED = "mixed"                        # מעורב
    SECULAR = "secular"                    # חילוני


class DriverVerificationStatus(str, enum.Enum):
    """סטטוס אימות נהג"""
    UNVERIFIED = "unverified"  # ברירת מחדל — לא עבר בדיקה
    PENDING = "pending"        # הוגש סלפי/תעודה, ממתין לאדמין
    APPROVED = "approved"      # אושר
    REJECTED = "rejected"      # נדחה (עם הערת דחייה)


class DriverSubscriptionStatus(str, enum.Enum):
    """סטטוס מנוי נהג"""
    TRIAL = "trial"        # תקופת ניסיון (7 ימים)
    ACTIVE = "active"      # מנוי פעיל (שולם)
    EXPIRED = "expired"    # פג תוקף
    PAUSED = "paused"      # מושהה זמנית
    CANCELLED = "cancelled"  # בוטל


class DriverProfile(Base):
    """פרופיל נהג — טבלה ראשית למידע אישי, רכב, אימות ומנוי"""

    __tablename__ = "driver_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )

    # פרטי רישום
    birth_date = Column(Date, nullable=False)
    vehicle_description = Column(Text, nullable=False)  # תיאור חופשי: "סיינה 2025 חדישה"
    vehicle_category = Column(String(50), nullable=False)  # ערך מ-VehicleCategory
    dress_code = Column(String(50), nullable=False)         # ערך מ-DressCode

    # אימות (מתמלא בסשן 3)
    verification_status = Column(
        String(50), nullable=False, default=DriverVerificationStatus.UNVERIFIED.value,
    )

    # מנוי
    subscription_status = Column(
        String(50), nullable=False, default=DriverSubscriptionStatus.TRIAL.value,
    )
    trial_starts_at = Column(DateTime, nullable=True)
    trial_expires_at = Column(DateTime, nullable=True)
    subscription_start_at = Column(DateTime, nullable=True)
    subscription_expires_at = Column(DateTime, nullable=True)

    # חותמות זמן
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # קשרים
    user = relationship("User", foreign_keys=[user_id])
