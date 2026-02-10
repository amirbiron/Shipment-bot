"""
Courier Approval Service - לוגיקת אישור/דחייה משותפת לטלגרם ווואטסאפ
"""
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.user import User, UserRole, ApprovalStatus
from app.domain.services.admin_notification_service import AdminNotificationService
from app.core.logging import get_logger
from app.core.validation import TextSanitizer

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

        # שליח שלחץ # בזמן המתנה לאישור חזר להיות SENDER -
        # עדיין מאפשרים אישור אם יש לו סטטוס PENDING ומחזירים ל-COURIER
        if user.role == UserRole.SENDER and user.approval_status == ApprovalStatus.PENDING:
            logger.info(
                "Approving courier who reverted to sender via #",
                extra_data={"user_id": user_id}
            )
            user.role = UserRole.COURIER

        if user.role != UserRole.COURIER:
            return ApprovalResult(False, f"❌ משתמש {user_id} אינו נהג")

        if user.approval_status == ApprovalStatus.APPROVED:
            return ApprovalResult(
                False,
                f"ℹ️ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) כבר מאושר"
            )

        if user.approval_status == ApprovalStatus.BLOCKED:
            return ApprovalResult(
                False,
                f"⛔ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) חסום במערכת. לא ניתן לאשר משתמש חסום."
            )

        user.approval_status = ApprovalStatus.APPROVED
        await db.commit()

        logger.info(
            "Courier approved",
            extra_data={"user_id": user_id, "name": user.full_name or user.name or 'לא צוין'}
        )

        return ApprovalResult(
            True,
            f"✅ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) אושר בהצלחה!",
            user
        )

    @staticmethod
    async def reject(db: AsyncSession, user_id: int, rejection_note: Optional[str] = None) -> ApprovalResult:
        """דחיית שליח לפי user_id. מחזיר תוצאה עם הודעה מתאימה."""
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            return ApprovalResult(False, f"❌ לא נמצא משתמש עם מזהה {user_id}")

        # שליח שלחץ # בזמן המתנה לאישור חזר להיות SENDER -
        # עדיין מאפשרים דחייה אם יש לו סטטוס PENDING ומחזירים ל-COURIER
        if user.role == UserRole.SENDER and user.approval_status == ApprovalStatus.PENDING:
            logger.info(
                "Rejecting courier who reverted to sender via #",
                extra_data={"user_id": user_id}
            )
            user.role = UserRole.COURIER

        if user.role != UserRole.COURIER:
            return ApprovalResult(False, f"❌ משתמש {user_id} אינו נהג")

        if user.approval_status == ApprovalStatus.APPROVED:
            return ApprovalResult(
                False,
                f"ℹ️ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) כבר מאושר. לא ניתן לדחות נהג מאושר."
            )

        if user.approval_status == ApprovalStatus.REJECTED:
            return ApprovalResult(
                False,
                f"ℹ️ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) כבר נדחה"
            )

        if user.approval_status == ApprovalStatus.BLOCKED:
            return ApprovalResult(
                False,
                f"⛔ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) חסום במערכת. לא ניתן לשנות סטטוס של משתמש חסום."
            )

        user.approval_status = ApprovalStatus.REJECTED
        # שמירת הערת דחייה — תמיד מעדכנים (גם None) למניעת הערה ישנה שנשארת
        user.rejection_note = rejection_note
        await db.commit()

        logger.info(
            "Courier rejected",
            extra_data={
                "user_id": user_id,
                "name": user.full_name or user.name or 'לא צוין',
                "has_rejection_note": bool(rejection_note),
            }
        )

        note_suffix = f"\nהערה: {rejection_note}" if rejection_note else ""
        return ApprovalResult(
            True,
            f"❌ נהג {user_id} ({user.full_name or user.name or 'לא צוין'}) נדחה.{note_suffix}",
            user
        )

    @staticmethod
    async def notify_after_decision(
        user: User,
        action: str,
        admin_name: str,
        send_telegram_fn: Optional[Callable[..., Awaitable]] = None,
        send_whatsapp_fn: Optional[Callable[..., Awaitable]] = None,
        rejection_note: Optional[str] = None,
    ) -> None:
        """
        שליחת הודעות לאחר החלטת אישור/דחייה:
        1. הודעה לשליח (בפלטפורמה המתאימה)
        2. סיכום לקבוצת מנהלים
        """
        # הודעות לשליח - בשני פורמטים (HTML לטלגרם, Markdown לוואטסאפ)
        # חישוב הערת דחייה מראש — נדרש גם בהודעה לשליח וגם בסיכום לקבוצה
        note = (rejection_note or user.rejection_note) if action != "approve" else None

        if action == "approve":
            tg_msg = """🎉 <b>חשבונך אושר!</b>

ברוכים הבאים למערכת השליחים!
מעכשיו תוכל לתפוס משלוחים ולהתחיל לעבוד.

כתוב <b>תפריט</b> כדי להתחיל."""
            wa_msg = """🎉 *חשבונך אושר!*

ברוכים הבאים למערכת השליחים!
מעכשיו תוכל לתפוס משלוחים ולהתחיל לעבוד.

כתוב *תפריט* כדי להתחיל."""
        else:
            # הודעת דחייה עם הערת מנהל (אם קיימת)
            if note:
                safe_note_html = TextSanitizer.sanitize_for_html(note)
                tg_msg = f"""😔 <b>לצערנו, בקשתך להצטרף כשליח נדחתה.</b>

📝 <b>הערת המנהל:</b> {safe_note_html}

אם אתה חושב שזו טעות, אנא צור קשר עם התמיכה."""
                wa_msg = f"""😔 *לצערנו, בקשתך להצטרף כשליח נדחתה.*

📝 *הערת המנהל:* {note}

אם אתה חושב שזו טעות, אנא צור קשר עם התמיכה."""
            else:
                tg_msg = """😔 <b>לצערנו, בקשתך להצטרף כשליח נדחתה.</b>

אם אתה חושב שזו טעות, אנא צור קשר עם התמיכה."""
                wa_msg = """😔 *לצערנו, בקשתך להצטרף כשליח נדחתה.*

אם אתה חושב שזו טעות, אנא צור קשר עם התמיכה."""

        # שליחה לשליח - זיהוי פלטפורמה עם סדר עקבי
        if user.telegram_chat_id and send_telegram_fn:
            await send_telegram_fn(user.telegram_chat_id, tg_msg)
        elif (
            user.phone_number
            and not user.phone_number.startswith("tg:")
            and not user.phone_number.endswith("@g.us")
            and send_whatsapp_fn
        ):
            await send_whatsapp_fn(user.phone_number, wa_msg)

        # סיכום לקבוצת מנהלים
        decision = "approved" if action == "approve" else "rejected"
        await AdminNotificationService.notify_group_courier_decision(
            user.id,
            user.full_name or user.name or 'לא צוין',
            user.service_area or 'לא צוין',
            user.vehicle_category,
            user.platform or 'telegram',
            decision,
            admin_name,
            rejection_note=note,
        )
