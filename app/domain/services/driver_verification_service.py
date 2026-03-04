"""
שירות אימות נהג (iDriver) — סשן 3

מנהל את זרימת האימות לנהגים מזרם חרדי:
1. קבלת סלפי
2. קבלת תעודת זהות
3. אישור/דחייה על ידי מנהל
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.user import User
from app.db.models.driver_profile import (
    DriverProfile,
    DriverVerificationStatus,
)
from app.core.logging import get_logger
from app.core.exceptions import ValidationException, NotFoundException
from app.core.validation import TextSanitizer

logger = get_logger(__name__)


@dataclass
class DriverApprovalResult:
    """תוצאת פעולת אישור/דחיית נהג"""
    success: bool
    message: str
    user: Optional[User] = None
    profile: Optional[DriverProfile] = None


class DriverVerificationService:
    """שירות אימות נהג — לוגיקה עסקית בלבד, ללא ניהול מצבים"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def submit_selfie(self, user_id: int, selfie_file_id: str) -> None:
        """
        שמירת סלפי אימות.

        Args:
            user_id: מזהה המשתמש
            selfie_file_id: מזהה קובץ התמונה מטלגרם/וואטסאפ

        Raises:
            ValidationException: מזהה קובץ ריק
            NotFoundException: משתמש או פרופיל לא נמצא
        """
        if not selfie_file_id or not selfie_file_id.strip():
            raise ValidationException("יש לשלוח תמונת סלפי", field="selfie")

        profile = await self._get_profile(user_id)
        profile.verification_selfie_file_id = selfie_file_id
        await self.db.commit()

        logger.info(
            "סלפי אימות נשמר",
            extra_data={"user_id": user_id},
        )

    async def submit_id_document(self, user_id: int, id_file_id: str) -> None:
        """
        שמירת תעודת זהות ועדכון סטטוס ל-PENDING.

        Args:
            user_id: מזהה המשתמש
            id_file_id: מזהה קובץ התמונה מטלגרם/וואטסאפ

        Raises:
            ValidationException: מזהה קובץ ריק
            NotFoundException: משתמש או פרופיל לא נמצא
        """
        if not id_file_id or not id_file_id.strip():
            raise ValidationException("יש לשלוח תמונת תעודת זהות", field="id_document")

        profile = await self._get_profile(user_id)
        profile.verification_id_file_id = id_file_id
        profile.verification_status = DriverVerificationStatus.PENDING.value
        await self.db.commit()

        logger.info(
            "תעודת זהות נשמרה וסטטוס עודכן ל-PENDING",
            extra_data={"user_id": user_id},
        )

    @staticmethod
    async def approve_driver(db: AsyncSession, user_id: int) -> DriverApprovalResult:
        """
        אישור נהג לפי user_id.

        Args:
            db: סשן DB
            user_id: מזהה המשתמש

        Returns:
            DriverApprovalResult עם הודעה מתאימה
        """
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return DriverApprovalResult(False, f"❌ לא נמצא משתמש עם מזהה {user_id}")

        profile_result = await db.execute(
            select(DriverProfile).where(DriverProfile.user_id == user_id)
        )
        profile = profile_result.scalar_one_or_none()
        if not profile:
            return DriverApprovalResult(False, f"❌ לא נמצא פרופיל נהג למשתמש {user_id}")

        if profile.verification_status == DriverVerificationStatus.APPROVED.value:
            return DriverApprovalResult(
                False,
                f"ℹ️ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) כבר מאושר",
            )

        if profile.verification_status != DriverVerificationStatus.PENDING.value:
            return DriverApprovalResult(
                False,
                f"❌ נהג {user_id} אינו ממתין לאישור (סטטוס: {profile.verification_status})",
            )

        profile.verification_status = DriverVerificationStatus.APPROVED.value
        profile.rejection_reason = None
        await db.commit()

        logger.info(
            "נהג אושר",
            extra_data={
                "user_id": user_id,
                "name": user.full_name or user.name or "לא צוין",
            },
        )

        return DriverApprovalResult(
            True,
            f"✅ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) אושר בהצלחה!",
            user,
            profile,
        )

    @staticmethod
    async def reject_driver(
        db: AsyncSession, user_id: int, rejection_reason: Optional[str] = None
    ) -> DriverApprovalResult:
        """
        דחיית נהג לפי user_id.

        Args:
            db: סשן DB
            user_id: מזהה המשתמש
            rejection_reason: סיבת הדחייה (אופציונלי)

        Returns:
            DriverApprovalResult עם הודעה מתאימה
        """
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return DriverApprovalResult(False, f"❌ לא נמצא משתמש עם מזהה {user_id}")

        profile_result = await db.execute(
            select(DriverProfile).where(DriverProfile.user_id == user_id)
        )
        profile = profile_result.scalar_one_or_none()
        if not profile:
            return DriverApprovalResult(False, f"❌ לא נמצא פרופיל נהג למשתמש {user_id}")

        if profile.verification_status == DriverVerificationStatus.APPROVED.value:
            return DriverApprovalResult(
                False,
                f"ℹ️ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) כבר מאושר. לא ניתן לדחות נהג מאושר.",
            )

        if profile.verification_status == DriverVerificationStatus.REJECTED.value:
            return DriverApprovalResult(
                False,
                f"ℹ️ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) כבר נדחה",
            )

        if profile.verification_status != DriverVerificationStatus.PENDING.value:
            return DriverApprovalResult(
                False,
                f"❌ נהג {user_id} אינו ממתין לאישור (סטטוס: {profile.verification_status})",
            )

        # נרמול מחרוזת ריקה/רווחים ל-None
        normalized_reason = (rejection_reason.strip() or None) if rejection_reason else None
        profile.verification_status = DriverVerificationStatus.REJECTED.value
        profile.rejection_reason = normalized_reason
        # ניקוי קבצי אימות כדי לאפשר הגשה מחדש
        profile.verification_selfie_file_id = None
        profile.verification_id_file_id = None
        await db.commit()

        logger.info(
            "נהג נדחה",
            extra_data={
                "user_id": user_id,
                "name": user.full_name or user.name or "לא צוין",
                "has_reason": bool(normalized_reason),
            },
        )

        note_suffix = TextSanitizer.format_note_line(normalized_reason, label="סיבה")
        return DriverApprovalResult(
            True,
            f"❌ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) נדחה.{note_suffix}",
            user,
            profile,
        )

    # ==================== מתודות פנימיות ====================

    async def _get_profile(self, user_id: int) -> DriverProfile:
        """שליפת פרופיל נהג לפי user_id"""
        result = await self.db.execute(
            select(DriverProfile).where(DriverProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise NotFoundException("DriverProfile", user_id)
        return profile
