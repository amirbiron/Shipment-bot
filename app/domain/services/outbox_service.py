"""
Outbox Service - Transactional Outbox Pattern for Async Messaging

This service implements the transactional outbox pattern to ensure
reliable message delivery without blocking the main transaction.
"""
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.outbox_message import OutboxMessage, MessagePlatform, MessageStatus
from app.db.models.delivery import Delivery
from app.core.config import settings


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

    async def queue_delivery_broadcast(self, delivery: Delivery) -> List[OutboxMessage]:
        """
        Queue broadcast messages for a new delivery to all couriers.
        Called within the delivery creation transaction.
        """
        messages = []

        # Create broadcast message content using secure token for smart links
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

        # Optional WhatsApp group broadcast (single group chat)
        group_id = (settings.WHATSAPP_COURIER_GROUP_ID or "").strip()
        if group_id:
            group_msg = await self.queue_message(
                platform=MessagePlatform.WHATSAPP,
                recipient_id=group_id,
                message_type="delivery_broadcast_group",
                message_content=content
            )
            messages.append(group_msg)

        # Queue for WhatsApp broadcast (placeholder recipient - actual recipients resolved by worker)
        whatsapp_msg = await self.queue_message(
            platform=MessagePlatform.WHATSAPP,
            recipient_id="BROADCAST_COURIERS",
            message_type="delivery_broadcast",
            message_content=content
        )
        messages.append(whatsapp_msg)

        # Queue for Telegram broadcast
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

        content = {
            "delivery_id": delivery.id,
            "courier_id": courier_id,
            "message_text": (
                f"✅ המשלוח #{delivery.id} נתפס!\n\n"
                f"📍 איסוף: {delivery.pickup_address}\n"
                f"🎯 יעד: {delivery.dropoff_address}"
            )
        }

        # Notify sender
        sender_msg = await self.queue_message(
            platform=MessagePlatform.WHATSAPP,  # Determine from sender preferences
            recipient_id=str(delivery.sender_id),
            message_type="capture_notification_sender",
            message_content=content
        )
        messages.append(sender_msg)

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
        from datetime import datetime
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
        from datetime import datetime, timedelta
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
                message.next_retry_at = datetime.utcnow() + timedelta(
                    seconds=30 * (2 ** message.retry_count)
                )

            await self.db.commit()
