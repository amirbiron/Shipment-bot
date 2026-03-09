"""
Outbox Service - Transactional Outbox Pattern for Async Messaging

This service implements the transactional outbox pattern to ensure
reliable message delivery without blocking the main transaction.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.outbox_message import OutboxMessage, MessagePlatform, MessageStatus
from app.db.models.dead_letter_message import DeadLetterMessage, DeadLetterStatus
from app.db.models.delivery import Delivery
from app.db.models.station import Station
from app.db.models.user import User

logger = get_logger(__name__)


def _calculate_backoff_seconds(
    retry_count: int,
    *,
    base_seconds: int,
    max_backoff_seconds: int,
) -> int:
    """
    Calculate exponential backoff seconds with a hard upper bound.

    Uses the same semantics as the previous implementation:
        backoff = base_seconds * (2 ** retry_count)

    The result is capped at max_backoff_seconds and avoids computing huge
    powers when retry_count is unexpectedly large.
    """
    if retry_count < 0:
        retry_count = 0

    if base_seconds <= 0 or max_backoff_seconds <= 0:
        return 0

    # If we already reached/exceeded max, return it.
    if base_seconds >= max_backoff_seconds:
        return max_backoff_seconds

    # We need to know whether 2**retry_count >= ceil(max/base) without computing 2**retry_count.
    required_multiplier = (max_backoff_seconds + base_seconds - 1) // base_seconds  # ceil div
    is_power_of_two = (required_multiplier & (required_multiplier - 1)) == 0
    threshold = required_multiplier.bit_length() - 1
    if not is_power_of_two:
        threshold += 1

    if retry_count >= threshold:
        return max_backoff_seconds

    backoff = base_seconds * (1 << retry_count)
    return min(backoff, max_backoff_seconds)


class OutboxService:
    """
    Service for managing outbox messages.

    Instead of synchronous sends, stores pending broadcast events in an
    Outbox table. Background workers process these asynchronously.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def queue_message(
        self,
        platform: MessagePlatform,
        recipient_id: str,
        message_type: str,
        message_content: dict
    ) -> OutboxMessage:
        """Queue a single message for delivery"""
        message = OutboxMessage(
            platform=platform,
            recipient_id=recipient_id,
            message_type=message_type,
            message_content=message_content,
            status=MessageStatus.PENDING
        )
        self.db.add(message)
        return message

    async def queue_delivery_broadcast(
        self, delivery: Delivery, station: Station | None = None
    ) -> List[OutboxMessage]:
        """
        שידור משלוח חדש.

        שלב 4: אם למשלוח יש תחנה עם קבוצה ציבורית — שידור לקבוצה.
        אחרת — שידור פרטני לכל השליחים (תואם לאחור).
        """
        messages = []

        # תוכן השידור — כולל קישור wa.me במצב היברידי
        from app.domain.services.whatsapp.wa_me_links import generate_capture_link

        capture_link = generate_capture_link(delivery.token)
        if capture_link:
            capture_instruction = f"🔗 לתפיסת המשלוח:\n{capture_link}"
        else:
            capture_instruction = f"לתפיסת המשלוח הקלידו: /capture {delivery.token}"

        content = {
            "delivery_id": delivery.id,
            "token": delivery.token,
            "pickup_address": delivery.pickup_address,
            "dropoff_address": delivery.dropoff_address,
            "fee": delivery.fee,
            "message_text": (
                f"🚚 משלוח חדש זמין!\n\n"
                f"📍 איסוף: {escape(delivery.pickup_address)}\n"
                f"🎯 יעד: {escape(delivery.dropoff_address)}\n"
                f"💰 עמלה: {delivery.fee}₪\n\n"
                f"{capture_instruction}"
            )
        }

        # שלב 4: שידור לקבוצה ציבורית של התחנה
        if station and station.public_group_chat_id:
            platform = MessagePlatform(
                station.public_group_platform or "telegram"
            )
            msg = await self.queue_message(
                platform=platform,
                recipient_id=station.public_group_chat_id,
                message_type="delivery_broadcast",
                message_content=content,
            )
            messages.append(msg)
            return messages

        # fallback: שידור פרטני לכל השליחים
        whatsapp_msg = await self.queue_message(
            platform=MessagePlatform.WHATSAPP,
            recipient_id="BROADCAST_COURIERS",
            message_type="delivery_broadcast",
            message_content=content
        )
        messages.append(whatsapp_msg)

        telegram_msg = await self.queue_message(
            platform=MessagePlatform.TELEGRAM,
            recipient_id="BROADCAST_COURIERS",
            message_type="delivery_broadcast",
            message_content=content
        )
        messages.append(telegram_msg)

        return messages

    async def queue_capture_notification(
        self,
        delivery: Delivery,
        courier_id: int
    ) -> List[OutboxMessage]:
        """Queue notifications when a delivery is captured"""
        messages = []

        # שליפת פרטי השולח לקביעת פלטפורמה ומזהה נמען
        sender_result = await self.db.execute(
            select(User).where(User.id == delivery.sender_id)
        )
        sender = sender_result.scalar_one_or_none()
        if not sender:
            return messages

        # קביעת פלטפורמה ומזהה נמען לפי פרטי השולח
        if sender.telegram_chat_id:
            platform = MessagePlatform.TELEGRAM
            recipient = sender.telegram_chat_id
        else:
            platform = MessagePlatform.WHATSAPP
            recipient = sender.phone_number

        if not recipient:
            return messages

        content = {
            "delivery_id": delivery.id,
            "courier_id": courier_id,
            "message_text": (
                f"✅ המשלוח #{delivery.id} נתפס!\n\n"
                f"📍 איסוף: {delivery.pickup_address}\n"
                f"🎯 יעד: {delivery.dropoff_address}"
            )
        }

        sender_msg = await self.queue_message(
            platform=platform,
            recipient_id=recipient,
            message_type="capture_notification_sender",
            message_content=content
        )
        messages.append(sender_msg)

        return messages

    # ==================== שלב 4: מתודות זרימת אישור ====================

    async def queue_delivery_request_to_dispatchers(
        self, delivery: Delivery, courier: User, station_id: int
    ) -> List[OutboxMessage]:
        """שלב 4: הודעה לסדרנים על בקשת נהג עם כפתורי אישור/דחייה"""
        from html import escape

        courier_name = escape(
            courier.full_name or courier.name or "לא צוין"
        )
        message_text = (
            f"📬 <b>בקשת משלוח חדשה!</b>\n\n"
            f"📦 משלוח #{delivery.id}\n"
            f"📍 איסוף: {escape(delivery.pickup_address)}\n"
            f"🎯 יעד: {escape(delivery.dropoff_address)}\n"
            f"💰 עמלה: {delivery.fee:.0f}₪\n\n"
            f"🚚 נהג מבקש: {courier_name}\n\n"
            f"לאישור: אשר משלוח {delivery.id}\n"
            f"לדחייה: דחה משלוח {delivery.id}"
        )

        content = {
            "delivery_id": delivery.id,
            "courier_id": courier.id,
            "station_id": station_id,
            "message_type": "delivery_request_notification",
            "message_text": message_text,
            # כפתורי inline לטלגרם
            "inline_keyboard": [
                [
                    {"text": "✅ אשר", "callback_data": f"approve_delivery_{delivery.id}"},
                    {"text": "❌ דחה", "callback_data": f"reject_delivery_{delivery.id}"},
                ]
            ],
        }

        messages = []
        for platform in [MessagePlatform.TELEGRAM, MessagePlatform.WHATSAPP]:
            msg = await self.queue_message(
                platform=platform,
                recipient_id=f"BROADCAST_DISPATCHERS_{station_id}",
                message_type="delivery_request_notification",
                message_content=content,
            )
            messages.append(msg)
        return messages

    async def queue_delivery_decision_notification(
        self, delivery: Delivery, courier: User, message_text: str
    ) -> List[OutboxMessage]:
        """שלב 4: הודעה לשליח על החלטת הסדרן (אישור/דחייה)"""
        messages = []
        content = {
            "delivery_id": delivery.id,
            "message_text": message_text,
        }

        # שליחה לפלטפורמה של השליח
        platform_str = courier.platform or "telegram"
        platform = MessagePlatform(platform_str)

        recipient_id = (
            courier.telegram_chat_id
            if platform == MessagePlatform.TELEGRAM
            else courier.phone_number
        )

        if recipient_id:
            msg = await self.queue_message(
                platform=platform,
                recipient_id=str(recipient_id),
                message_type="delivery_decision_notification",
                message_content=content,
            )
            messages.append(msg)
        return messages

    async def queue_closed_card(
        self, station: Station, card_text: str
    ) -> List[OutboxMessage]:
        """שלב 4: שליחת כרטיס סגור לקבוצה פרטית של התחנה"""
        messages = []

        if not station.private_group_chat_id:
            return messages

        platform = MessagePlatform(
            station.private_group_platform or "telegram"
        )
        content = {
            "message_text": card_text,
        }

        msg = await self.queue_message(
            platform=platform,
            recipient_id=station.private_group_chat_id,
            message_type="closed_shipment_card",
            message_content=content,
        )
        messages.append(msg)
        return messages

    async def queue_auto_cancel_notification(
        self, delivery: Delivery
    ) -> List[OutboxMessage]:
        """שליחת התראה לשולח על ביטול אוטומטי של משלוח שלא נתפס."""
        messages = []

        sender_result = await self.db.execute(
            select(User).where(User.id == delivery.sender_id)
        )
        sender = sender_result.scalar_one_or_none()
        if not sender:
            return messages

        # קביעת פלטפורמה ונמען
        if sender.telegram_chat_id:
            platform = MessagePlatform.TELEGRAM
            recipient = sender.telegram_chat_id
        else:
            platform = MessagePlatform.WHATSAPP
            recipient = sender.phone_number

        if not recipient:
            return messages

        content = {
            "delivery_id": delivery.id,
            "message_text": (
                f"⏰ המשלוח #{delivery.id} בוטל אוטומטית\n\n"
                f"המשלוח לא נתפס על ידי שליח בזמן שהוקצב ולכן בוטל.\n\n"
                f"📍 איסוף: {escape(delivery.pickup_address)}\n"
                f"🎯 יעד: {escape(delivery.dropoff_address)}\n\n"
                f"ניתן ליצור משלוח חדש מהתפריט הראשי."
            ),
        }

        msg = await self.queue_message(
            platform=platform,
            recipient_id=recipient,
            message_type="auto_cancel_notification",
            message_content=content,
        )
        messages.append(msg)
        return messages

    async def queue_expiry_warning(
        self, delivery: Delivery, minutes_remaining: int
    ) -> List[OutboxMessage]:
        """שליחת התראה לשולח שהמשלוח עומד לפוג בעוד X דקות."""
        messages = []

        sender_result = await self.db.execute(
            select(User).where(User.id == delivery.sender_id)
        )
        sender = sender_result.scalar_one_or_none()
        if not sender:
            return messages

        if sender.telegram_chat_id:
            platform = MessagePlatform.TELEGRAM
            recipient = sender.telegram_chat_id
        else:
            platform = MessagePlatform.WHATSAPP
            recipient = sender.phone_number

        if not recipient:
            return messages

        content = {
            "delivery_id": delivery.id,
            "message_text": (
                f"⚠️ המשלוח #{delivery.id} עומד להתבטל בעוד {minutes_remaining} דקות!\n\n"
                f"📍 איסוף: {escape(delivery.pickup_address)}\n"
                f"🎯 יעד: {escape(delivery.dropoff_address)}\n\n"
                f"אם אף שליח לא יתפוס את המשלוח — הוא יבוטל אוטומטית."
            ),
        }

        msg = await self.queue_message(
            platform=platform,
            recipient_id=recipient,
            message_type="expiry_warning",
            message_content=content,
        )
        messages.append(msg)
        return messages

    async def get_pending_messages(self, limit: int = 100) -> List[OutboxMessage]:
        """שליפת הודעות ממתינות לעיבוד.

        מסנן ברמת SQL גם לפי next_retry_at כדי לנצל את האינדקס החלקי
        idx_outbox_next_retry (ראה schema.sql) ולמנוע שליפת הודעות
        שעדיין לא הגיע זמן ה-retry שלהן.
        """
        now = datetime.utcnow()
        result = await self.db.execute(
            select(OutboxMessage)
            .where(
                OutboxMessage.status == MessageStatus.PENDING,
                or_(
                    OutboxMessage.next_retry_at.is_(None),
                    OutboxMessage.next_retry_at <= now,
                ),
            )
            .order_by(OutboxMessage.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_as_processing(self, message_id: int) -> None:
        """Mark message as being processed"""
        result = await self.db.execute(
            select(OutboxMessage).where(OutboxMessage.id == message_id)
        )
        message = result.scalar_one_or_none()
        if message:
            message.status = MessageStatus.PROCESSING
            await self.db.commit()

    async def mark_as_sent(self, message_id: int) -> None:
        """Mark message as successfully sent"""
        result = await self.db.execute(
            select(OutboxMessage).where(OutboxMessage.id == message_id)
        )
        message = result.scalar_one_or_none()
        if message:
            message.status = MessageStatus.SENT
            message.processed_at = datetime.utcnow()
            await self.db.commit()

    async def mark_as_failed(
        self, message_id: int, error: str, *, is_transient: bool = True
    ) -> None:
        """סימון הודעה ככושלת.

        Args:
            message_id: מזהה ההודעה
            error: תיאור השגיאה
            is_transient: האם השגיאה זמנית (retry) או קבועה (dead letter מיידי)
        """
        result = await self.db.execute(
            select(OutboxMessage).where(OutboxMessage.id == message_id)
        )
        message = result.scalar_one_or_none()
        if message:
            message.retry_count += 1
            message.last_error = error

            # שגיאה קבועה (4xx) → dead letter מיידי, בלי retry נוסף
            permanently_failed = (
                not is_transient or message.retry_count >= message.max_retries
            )

            if permanently_failed:
                message.status = MessageStatus.FAILED
                # העברה ל-dead letter queue
                await self._move_to_dead_letter(
                    message,
                    failure_reason="permanent" if not is_transient else "max_retries_exceeded",
                )
            else:
                message.status = MessageStatus.PENDING
                # Exponential backoff for retry
                backoff_seconds = _calculate_backoff_seconds(
                    message.retry_count,
                    base_seconds=settings.OUTBOX_RETRY_BASE_SECONDS,
                    max_backoff_seconds=settings.OUTBOX_MAX_BACKOFF_SECONDS,
                )
                message.next_retry_at = datetime.utcnow() + timedelta(
                    seconds=backoff_seconds
                )

            await self.db.commit()

    async def _move_to_dead_letter(
        self, message: OutboxMessage, failure_reason: str
    ) -> None:
        """העברת הודעה שנכשלה סופית ל-dead letter queue."""
        dead_letter = DeadLetterMessage(
            original_message_id=message.id,
            platform=message.platform.value if isinstance(message.platform, MessagePlatform) else message.platform,
            recipient_id=message.recipient_id,
            message_type=message.message_type,
            message_content=message.message_content,
            retry_count=message.retry_count,
            last_error=message.last_error,
            failure_reason=failure_reason,
            status=DeadLetterStatus.FAILED,
            original_created_at=message.created_at,
        )
        self.db.add(dead_letter)
        logger.warning(
            "הודעה הועברה ל-dead letter queue",
            extra_data={
                "message_id": message.id,
                "platform": str(message.platform),
                "message_type": message.message_type,
                "retry_count": message.retry_count,
                "failure_reason": failure_reason,
            },
        )

    async def retry_dead_letter(self, dead_letter_id: int) -> OutboxMessage | None:
        """שליחה חוזרת של הודעה מ-dead letter queue.

        יוצרת הודעת outbox חדשה ומסמנת את ה-dead letter כ-retried.
        """
        result = await self.db.execute(
            select(DeadLetterMessage).where(
                DeadLetterMessage.id == dead_letter_id,
                DeadLetterMessage.status == DeadLetterStatus.FAILED,
            )
        )
        dead_letter = result.scalar_one_or_none()
        if not dead_letter:
            return None

        # יצירת הודעת outbox חדשה
        new_message = OutboxMessage(
            platform=MessagePlatform(dead_letter.platform),
            recipient_id=dead_letter.recipient_id,
            message_type=dead_letter.message_type,
            message_content=dead_letter.message_content,
            status=MessageStatus.PENDING,
            retry_count=0,
        )
        self.db.add(new_message)

        # סימון כ-retried
        dead_letter.status = DeadLetterStatus.RETRIED
        dead_letter.retried_at = datetime.utcnow()

        await self.db.commit()
        await self.db.refresh(new_message)

        logger.info(
            "הודעה מ-dead letter queue נשלחה מחדש",
            extra_data={
                "dead_letter_id": dead_letter_id,
                "new_message_id": new_message.id,
            },
        )
        return new_message

    async def get_dead_letter_messages(
        self, limit: int = 50, offset: int = 0
    ) -> List[DeadLetterMessage]:
        """שליפת הודעות מ-dead letter queue."""
        result = await self.db.execute(
            select(DeadLetterMessage)
            .where(DeadLetterMessage.status == DeadLetterStatus.FAILED)
            .order_by(DeadLetterMessage.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_dead_letter_count(self) -> int:
        """מספר הודעות כושלות ב-dead letter queue."""
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.count(DeadLetterMessage.id)).where(
                DeadLetterMessage.status == DeadLetterStatus.FAILED
            )
        )
        return result.scalar() or 0
