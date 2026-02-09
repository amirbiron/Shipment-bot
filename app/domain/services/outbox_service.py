"""
Outbox Service - Transactional Outbox Pattern for Async Messaging

This service implements the transactional outbox pattern to ensure
reliable message delivery without blocking the main transaction.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.db.models.outbox_message import OutboxMessage, MessagePlatform, MessageStatus
from app.db.models.delivery import Delivery
from app.db.models.station import Station
from app.db.models.user import User


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

        # תוכן השידור עם קישור חכם
        content = {
            "delivery_id": delivery.id,
            "token": delivery.token,
            "pickup_address": delivery.pickup_address,
            "dropoff_address": delivery.dropoff_address,
            "fee": delivery.fee,
            "message_text": (
                f"🚚 משלוח חדש זמין!\n\n"
                f"📍 איסוף: {delivery.pickup_address}\n"
                f"🎯 יעד: {delivery.dropoff_address}\n"
                f"💰 עמלה: {delivery.fee}₪\n\n"
                f"לתפיסת המשלוח הקלידו: /capture {delivery.token}"
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

    async def get_pending_messages(self, limit: int = 100) -> List[OutboxMessage]:
        """Get pending messages for processing"""
        result = await self.db.execute(
            select(OutboxMessage)
            .where(OutboxMessage.status == MessageStatus.PENDING)
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

    async def mark_as_failed(self, message_id: int, error: str) -> None:
        """Mark message as failed with error"""
        result = await self.db.execute(
            select(OutboxMessage).where(OutboxMessage.id == message_id)
        )
        message = result.scalar_one_or_none()
        if message:
            message.retry_count += 1
            message.last_error = error

            if message.retry_count >= message.max_retries:
                message.status = MessageStatus.FAILED
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
