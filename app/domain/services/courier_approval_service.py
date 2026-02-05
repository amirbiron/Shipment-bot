"""
Courier Approval Service - לוגיקת אישור/דחייה משותפת לטלגרם ווואטסאפ
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.user import User, UserRole, ApprovalStatus
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ApprovalResult:
    """תוצאת פעולת אישור/דחייה"""
    success: bool
    message: str
    user: Optional[User] = None


class CourierApprovalService:
    """שירות אישור/דחיית שליחים - מקור אמת יחיד ללוגיקת ולידציה ועדכון DB"""

    @staticmethod
    async def approve(db: AsyncSession, user_id: int) -> ApprovalResult:
        """אישור שליח לפי user_id. מחזיר תוצאה עם הודעה מתאימה."""
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            return ApprovalResult(False, f"❌ לא נמצא משתמש עם מזהה {user_id}")

        if user.role != UserRole.COURIER:
            return ApprovalResult(False, f"❌ משתמש {user_id} אינו שליח")

        if user.approval_status == ApprovalStatus.APPROVED:
            return ApprovalResult(
                False,
                f"ℹ️ שליח {user_id} ({user.full_name or user.name or 'לא צוין'}) כבר מאושר"
            )

        if user.approval_status == ApprovalStatus.BLOCKED:
            return ApprovalResult(
                False,
                f"⛔ שליח {user_id} ({user.full_name or user.name or 'לא צוין'}) חסום במערכת. לא ניתן לאשר משתמש חסום."
            )

        user.approval_status = ApprovalStatus.APPROVED
        await db.commit()

        logger.info(
            "Courier approved",
            extra_data={"user_id": user_id, "name": user.full_name or user.name or 'לא צוין'}
        )

        return ApprovalResult(
            True,
            f"✅ שליח {user_id} ({user.full_name or user.name or 'לא צוין'}) אושר בהצלחה!",
            user
        )

    @staticmethod
    async def reject(db: AsyncSession, user_id: int) -> ApprovalResult:
        """דחיית שליח לפי user_id. מחזיר תוצאה עם הודעה מתאימה."""
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            return ApprovalResult(False, f"❌ לא נמצא משתמש עם מזהה {user_id}")

        if user.role != UserRole.COURIER:
            return ApprovalResult(False, f"❌ משתמש {user_id} אינו שליח")

        if user.approval_status == ApprovalStatus.REJECTED:
            return ApprovalResult(
                False,
                f"ℹ️ שליח {user_id} ({user.full_name or user.name or 'לא צוין'}) כבר נדחה"
            )

        if user.approval_status == ApprovalStatus.BLOCKED:
            return ApprovalResult(
                False,
                f"⛔ שליח {user_id} ({user.full_name or user.name or 'לא צוין'}) חסום במערכת. לא ניתן לשנות סטטוס של משתמש חסום."
            )

        user.approval_status = ApprovalStatus.REJECTED
        await db.commit()

        logger.info(
            "Courier rejected",
            extra_data={"user_id": user_id, "name": user.full_name or user.name or 'לא צוין'}
        )

        return ApprovalResult(
            True,
            f"❌ שליח {user_id} ({user.full_name or user.name or 'לא צוין'}) נדחה.",
            user
        )
