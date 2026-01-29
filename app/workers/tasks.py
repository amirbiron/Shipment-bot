"""
Celery Tasks for Async Message Processing

Implements the worker side of the Transactional Outbox pattern.
Processes pending messages from the outbox table and sends them
via WhatsApp or Telegram.
"""
import asyncio
from datetime import datetime, timedelta

from app.workers.celery_app import celery_app
from app.db.database import AsyncSessionLocal
from app.db.models.outbox_message import OutboxMessage, MessagePlatform, MessageStatus
from app.db.models.user import User, UserRole
from app.domain.services.outbox_service import OutboxService
from sqlalchemy import select


def run_async(coro):
    """Helper to run async code in sync Celery task"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _send_whatsapp_message(phone: str, content: dict) -> bool:
    """Send message via WhatsApp Gateway"""
    import httpx
    from app.core.config import settings

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.WHATSAPP_GATEWAY_URL}/send",
                json={
                    "phone": phone,
                    "message": content.get("message_text", "")
                },
                timeout=30.0
            )
            return response.status_code == 200
    except Exception as e:
        print(f"WhatsApp send error: {e}")
        return False


async def _send_telegram_message(chat_id: str, content: dict) -> bool:
    """Send message via Telegram Bot API"""
    import httpx
    from app.core.config import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        return False

    try:
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": content.get("message_text", ""),
                    "parse_mode": "HTML"
                },
                timeout=30.0
            )
            return response.status_code == 200
    except Exception as e:
        print(f"Telegram send error: {e}")
        return False


async def _get_courier_recipients(db, platform: MessagePlatform) -> list:
    """Get all active couriers for the given platform"""
    result = await db.execute(
        select(User).where(
            User.role == UserRole.COURIER,
            User.is_active == True,
            User.platform == platform.value
        )
    )
    return list(result.scalars().all())


async def _process_single_message(message: OutboxMessage) -> tuple:
    """Process a single outbox message"""
    async with AsyncSessionLocal() as db:
        outbox_service = OutboxService(db)

        # Mark as processing
        await outbox_service.mark_as_processing(message.id)

        try:
            content = message.message_content

            # Handle broadcast messages
            if message.recipient_id == "BROADCAST_COURIERS":
                recipients = await _get_courier_recipients(db, message.platform)
                success = True

                for recipient in recipients:
                    if message.platform == MessagePlatform.WHATSAPP:
                        result = await _send_whatsapp_message(
                            recipient.phone_number, content
                        )
                    else:
                        result = await _send_telegram_message(
                            recipient.telegram_chat_id, content
                        )
                    success = success and result

                if success:
                    await outbox_service.mark_as_sent(message.id)
                    return True, "Broadcast sent successfully"
                else:
                    await outbox_service.mark_as_failed(message.id, "Some recipients failed")
                    return False, "Partial broadcast failure"

            # Handle direct messages
            else:
                if message.platform == MessagePlatform.WHATSAPP:
                    success = await _send_whatsapp_message(
                        message.recipient_id, content
                    )
                else:
                    success = await _send_telegram_message(
                        message.recipient_id, content
                    )

                if success:
                    await outbox_service.mark_as_sent(message.id)
                    return True, "Message sent successfully"
                else:
                    await outbox_service.mark_as_failed(message.id, "Send failed")
                    return False, "Send failed"

        except Exception as e:
            await outbox_service.mark_as_failed(message.id, str(e))
            return False, str(e)


@celery_app.task(name="app.workers.tasks.process_outbox_messages")
def process_outbox_messages():
    """
    Process pending messages from the outbox.
    This task runs periodically to ensure reliable message delivery.
    """

    async def _process():
        async with AsyncSessionLocal() as db:
            outbox_service = OutboxService(db)
            messages = await outbox_service.get_pending_messages(limit=50)

            results = []
            for message in messages:
                # Skip messages that are scheduled for later retry
                if message.next_retry_at and message.next_retry_at > datetime.utcnow():
                    continue

                success, result = await _process_single_message(message)
                results.append({
                    "message_id": message.id,
                    "success": success,
                    "result": result
                })

            return results

    return run_async(_process())


@celery_app.task(name="app.workers.tasks.send_message")
def send_message(message_id: int):
    """Send a specific message by ID"""

    async def _send():
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(OutboxMessage).where(OutboxMessage.id == message_id)
            )
            message = result.scalar_one_or_none()

            if not message:
                return {"error": "Message not found"}

            success, result = await _process_single_message(message)
            return {"success": success, "result": result}

    return run_async(_send())


@celery_app.task(name="app.workers.tasks.broadcast_to_couriers")
def broadcast_to_couriers(message_text: str, delivery_id: int = None):
    """Broadcast a message to all active couriers"""

    async def _broadcast():
        async with AsyncSessionLocal() as db:
            # Get all couriers
            whatsapp_couriers = await _get_courier_recipients(db, MessagePlatform.WHATSAPP)
            telegram_couriers = await _get_courier_recipients(db, MessagePlatform.TELEGRAM)

            content = {
                "message_text": message_text,
                "delivery_id": delivery_id
            }

            results = []

            # Send to WhatsApp couriers
            for courier in whatsapp_couriers:
                success = await _send_whatsapp_message(courier.phone_number, content)
                results.append({
                    "courier_id": courier.id,
                    "platform": "whatsapp",
                    "success": success
                })

            # Send to Telegram couriers
            for courier in telegram_couriers:
                if courier.telegram_chat_id:
                    success = await _send_telegram_message(courier.telegram_chat_id, content)
                    results.append({
                        "courier_id": courier.id,
                        "platform": "telegram",
                        "success": success
                    })

            return {
                "total_sent": len(results),
                "successful": sum(1 for r in results if r["success"]),
                "results": results
            }

    return run_async(_broadcast())


@celery_app.task(name="app.workers.tasks.cleanup_old_messages")
def cleanup_old_messages(days: int = 30):
    """Clean up old processed messages from the outbox"""

    async def _cleanup():
        async with AsyncSessionLocal() as db:
            cutoff = datetime.utcnow() - timedelta(days=days)

            result = await db.execute(
                select(OutboxMessage).where(
                    OutboxMessage.status == MessageStatus.SENT,
                    OutboxMessage.processed_at < cutoff
                )
            )
            old_messages = result.scalars().all()

            count = len(old_messages)
            for msg in old_messages:
                await db.delete(msg)

            await db.commit()
            return {"deleted": count}

    return run_async(_cleanup())
