"""
Celery Tasks for Async Message Processing

Implements the worker side of the Transactional Outbox pattern.
Processes pending messages from the outbox table and sends them
via WhatsApp or Telegram.
"""
import asyncio
from datetime import datetime, timedelta

from app.workers.celery_app import celery_app
from app.db.database import get_task_session
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
    async with get_task_session() as db:
        outbox_service = OutboxService(db)

        # Mark as processing
        await outbox_service.mark_as_processing(message.id)

        try:
            content = message.message_content

            # Handle broadcast messages
            if message.recipient_id == "BROADCAST_COURIERS":
                recipients = await _get_courier_recipients(db, message.platform)

                # Send to all recipients in parallel for better performance
                if message.platform == MessagePlatform.WHATSAPP:
                    tasks = [
                        _send_whatsapp_message(r.phone_number, content)
                        for r in recipients
                    ]
                else:
                    tasks = [
                        _send_telegram_message(r.telegram_chat_id, content)
                        for r in recipients if r.telegram_chat_id
                    ]

                results = await asyncio.gather(*tasks, return_exceptions=True)
                success_count = sum(1 for r in results if r is True)
                total_count = len(results)

                if success_count == total_count:
                    await outbox_service.mark_as_sent(message.id)
                    return True, f"Broadcast sent to {success_count}/{total_count} recipients"
                elif success_count > 0:
                    await outbox_service.mark_as_sent(message.id)
                    return True, f"Partial broadcast: {success_count}/{total_count} succeeded"
                else:
                    await outbox_service.mark_as_failed(message.id, "All recipients failed")
                    return False, "Broadcast failed"

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
        async with get_task_session() as db:
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
        async with get_task_session() as db:
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
    """Broadcast a message to all active couriers using parallel sending"""

    async def _broadcast():
        async with get_task_session() as db:
            # Get all couriers
            whatsapp_couriers = await _get_courier_recipients(db, MessagePlatform.WHATSAPP)
            telegram_couriers = await _get_courier_recipients(db, MessagePlatform.TELEGRAM)

            content = {
                "message_text": message_text,
                "delivery_id": delivery_id
            }

            # Build task list for parallel execution
            async def send_to_courier(courier, platform: str):
                if platform == "whatsapp":
                    success = await _send_whatsapp_message(courier.phone_number, content)
                else:
                    if not courier.telegram_chat_id:
                        return {"courier_id": courier.id, "platform": platform, "success": False}
                    success = await _send_telegram_message(courier.telegram_chat_id, content)
                return {"courier_id": courier.id, "platform": platform, "success": success}

            # Create all tasks
            tasks = []
            for courier in whatsapp_couriers:
                tasks.append(send_to_courier(courier, "whatsapp"))
            for courier in telegram_couriers:
                tasks.append(send_to_courier(courier, "telegram"))

            # Execute all sends in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out exceptions and convert to proper results
            final_results = []
            for r in results:
                if isinstance(r, Exception):
                    final_results.append({"success": False, "error": str(r)})
                else:
                    final_results.append(r)

            return {
                "total_sent": len(final_results),
                "successful": sum(1 for r in final_results if r.get("success")),
                "results": final_results
            }

    return run_async(_broadcast())


@celery_app.task(name="app.workers.tasks.cleanup_old_messages")
def cleanup_old_messages(days: int = 30):
    """Clean up old processed messages from the outbox"""

    async def _cleanup():
        async with get_task_session() as db:
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
