"""
Celery Tasks for Async Message Processing

Implements the worker side of the Transactional Outbox pattern.
Processes pending messages from the outbox table and sends them
via WhatsApp or Telegram.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.workers.celery_app import celery_app
from app.db.database import get_task_session
from app.db.models.outbox_message import OutboxMessage, MessagePlatform, MessageStatus
from app.db.models.user import User, UserRole, ApprovalStatus
from app.domain.services.outbox_service import OutboxService
from app.core.logging import get_logger, set_correlation_id
from app.core.circuit_breaker import get_telegram_circuit_breaker, get_whatsapp_circuit_breaker
from app.core.exceptions import TelegramError, WhatsAppError
from app.core.validation import PhoneNumberValidator, convert_html_to_whatsapp
from sqlalchemy import select

logger = get_logger(__name__)


@contextmanager
def get_event_loop():
    """
    Context manager for proper event loop handling in Celery tasks.
    Creates a new event loop and ensures proper cleanup to prevent resource leaks.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        try:
            # Cancel all pending tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            # Wait for tasks to be cancelled
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()


def run_async(coro):
    """Helper to run async code in sync Celery task with proper cleanup"""
    # Set correlation ID for task tracking
    set_correlation_id()

    with get_event_loop() as loop:
        return loop.run_until_complete(coro)


async def _send_whatsapp_message(phone: str, content: dict) -> bool:
    """
    Send message via WhatsApp Gateway with circuit breaker protection.
    ממיר אוטומטית תגי HTML לפורמט וואטסאפ.
    """
    import httpx
    from app.core.config import settings

    # המרת תגי HTML לפורמט וואטסאפ (לדוגמה: <b> -> *)
    message_text = content.get("message_text", "")
    formatted_text = convert_html_to_whatsapp(message_text)

    circuit_breaker = get_whatsapp_circuit_breaker()

    async def _send():
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.WHATSAPP_GATEWAY_URL}/send",
                json={
                    "phone": phone,
                    "message": formatted_text
                },
                timeout=30.0
            )
            if response.status_code != 200:
                raise WhatsAppError.from_response(
                    "send",
                    response,
                    message=f"gateway /send returned status {response.status_code}",
                )
            return True

    try:
        return await circuit_breaker.execute(_send)
    except Exception as e:
        logger.error(
            "WhatsApp send error",
            extra_data={"phone": PhoneNumberValidator.mask(phone), "error": str(e)},
            exc_info=True
        )
        return False


async def _send_telegram_message(chat_id: str, content: dict) -> bool:
    """Send message via Telegram Bot API with circuit breaker protection"""
    import httpx
    from app.core.config import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram bot token not configured")
        return False

    circuit_breaker = get_telegram_circuit_breaker()

    async def _send():
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": content.get("message_text", ""),
            "parse_mode": "HTML",
        }
        # הוספת inline keyboard אם קיים בתוכן (שלב 4: כפתורי אישור/דחייה)
        if content.get("inline_keyboard"):
            payload["reply_markup"] = {
                "inline_keyboard": content["inline_keyboard"]
            }
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30.0)
            if response.status_code != 200:
                raise TelegramError.from_response(
                    "sendMessage",
                    response,
                    message=f"sendMessage returned status {response.status_code}",
                )
            return True

    try:
        return await circuit_breaker.execute(_send)
    except Exception as e:
        logger.error(
            "Telegram send error",
            extra_data={"chat_id": chat_id, "error": str(e)},
            exc_info=True
        )
        return False


async def _get_courier_recipients(db: "AsyncSession", platform: MessagePlatform) -> list[User]:
    """שליפת שליחים מאושרים ופעילים לפלטפורמה נתונה"""
    result = await db.execute(
        select(User).where(
            User.role == UserRole.COURIER,
            User.is_active == True,
            User.platform == platform.value,
            User.approval_status == ApprovalStatus.APPROVED,  # שלב 4: רק שליחים מאושרים
        )
    )
    return list(result.scalars().all())


async def _get_dispatcher_recipients(
    db: "AsyncSession", station_id: int, platform: MessagePlatform
) -> list[User]:
    """שלב 4: שליפת סדרנים פעילים של תחנה פעילה לפלטפורמה נתונה"""
    from app.db.models.station_dispatcher import StationDispatcher
    from app.db.models.station import Station

    result = await db.execute(
        select(User).join(
            StationDispatcher, StationDispatcher.user_id == User.id
        ).join(
            Station, StationDispatcher.station_id == Station.id
        ).where(
            StationDispatcher.station_id == station_id,
            StationDispatcher.is_active == True,
            Station.is_active == True,
            User.is_active == True,
            User.platform == platform.value,
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

                # אם אין נמענים - לא לסמן כנשלח, להחזיר שגיאה
                if not recipients:
                    logger.warning(
                        "Broadcast has no recipients",
                        extra_data={
                            "message_id": message.id,
                            "platform": message.platform.value
                        }
                    )
                    await outbox_service.mark_as_failed(
                        message.id,
                        "No recipients available for broadcast"
                    )
                    return False, "No recipients available for broadcast"

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

                # בדיקה נוספת אחרי סינון - רלוונטי ל-Telegram כשיש נמענים בלי chat_id
                if not tasks:
                    logger.warning(
                        "Broadcast has no valid recipients after filtering",
                        extra_data={
                            "message_id": message.id,
                            "platform": message.platform.value,
                            "total_recipients": len(recipients)
                        }
                    )
                    await outbox_service.mark_as_failed(
                        message.id,
                        f"No valid recipients (had {len(recipients)} without chat_id)"
                    )
                    return False, f"No valid recipients for {message.platform.value}"

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

            # שלב 4: שידור לסדרני תחנה
            elif message.recipient_id.startswith("BROADCAST_DISPATCHERS_"):
                station_id = int(message.recipient_id.split("_")[-1])
                recipients = await _get_dispatcher_recipients(
                    db, station_id, message.platform
                )

                if not recipients:
                    logger.warning(
                        "Dispatcher broadcast has no recipients",
                        extra_data={
                            "message_id": message.id,
                            "station_id": station_id,
                            "platform": message.platform.value,
                        }
                    )
                    await outbox_service.mark_as_failed(
                        message.id,
                        "No dispatchers available for station"
                    )
                    return False, "No dispatchers available"

                if message.platform == MessagePlatform.WHATSAPP:
                    tasks = [
                        _send_whatsapp_message(r.phone_number, content)
                        for r in recipients
                    ]
                else:
                    # לטלגרם: שליחה עם inline keyboard אם יש בתוכן
                    tasks = [
                        _send_telegram_message(
                            r.telegram_chat_id, content
                        )
                        for r in recipients if r.telegram_chat_id
                    ]

                if not tasks:
                    await outbox_service.mark_as_failed(
                        message.id,
                        f"No valid dispatcher recipients for {message.platform.value}"
                    )
                    return False, "No valid dispatcher recipients"

                results = await asyncio.gather(*tasks, return_exceptions=True)
                success_count = sum(1 for r in results if r is True)

                if success_count > 0:
                    await outbox_service.mark_as_sent(message.id)
                    return True, f"Sent to {success_count}/{len(results)} dispatchers"
                else:
                    await outbox_service.mark_as_failed(
                        message.id, "All dispatchers failed"
                    )
                    return False, "Dispatcher broadcast failed"

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

            # אם אין שליחים - להחזיר שגיאה במקום הצלחה ריקה
            if not tasks:
                logger.warning(
                    "Broadcast to couriers has no recipients",
                    extra_data={"delivery_id": delivery_id}
                )
                return {
                    "total_sent": 0,
                    "successful": 0,
                    "results": [],
                    "error": "No active couriers available for broadcast"
                }

            # Execute all sends in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # סינון exceptions עם לוג מפורט — מונע אובדן מידע דיאגנוסטי
            final_results = []
            for r in results:
                if isinstance(r, Exception):
                    logger.error(
                        "כשלון בשליחת broadcast לשליח",
                        extra_data={
                            "delivery_id": delivery_id,
                            "error": str(r),
                            "error_type": type(r).__name__,
                        },
                        exc_info=(type(r), r, r.__traceback__),
                    )
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


@celery_app.task(name="app.workers.tasks.cleanup_old_webhook_events")
def cleanup_old_webhook_events(days: int = 7):
    """ניקוי רשומות ישנות מטבלת webhook_events (idempotency)"""
    from sqlalchemy import delete as sa_delete
    from app.db.models.webhook_event import WebhookEvent

    async def _cleanup():
        async with get_task_session() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            result = await db.execute(
                sa_delete(WebhookEvent).where(
                    WebhookEvent.status == "completed",
                    WebhookEvent.created_at < cutoff,
                )
            )
            deleted = result.rowcount

            await db.commit()
            logger.info(
                "Cleaned up old webhook events",
                extra_data={"deleted": deleted, "cutoff_days": days},
            )
            return {"deleted": deleted}

    return run_async(_cleanup())


@celery_app.task(name="app.workers.tasks.process_billing_cycle_blocking")
def process_billing_cycle_blocking():
    """
    שלב 5: בדיקה יומית — חסימת נהגים שלא שילמו חודשיים רצופים.

    רץ יומית (idempotent) — בודק כל תחנה פעילה ומחסים אוטומטית
    נהגים עם חיובים שלא שולמו ב-2 מחזורי חיוב רצופים (28 ל-28).
    """
    from app.db.models.station import Station
    from app.domain.services.station_service import StationService

    async def _process():
        async with get_task_session() as db:
            result = await db.execute(
                select(Station).where(Station.is_active == True)  # noqa: E712
            )
            stations = list(result.scalars().all())

            total_blocked = 0
            for station in stations:
                try:
                    station_service = StationService(db)
                    blocked = await station_service.auto_block_unpaid_drivers(
                        station.id
                    )
                    total_blocked += len(blocked)
                except Exception as e:
                    logger.error(
                        "כשלון בבדיקת חסימה אוטומטית לתחנה",
                        extra_data={
                            "station_id": station.id,
                            "error": str(e),
                        },
                        exc_info=True,
                    )
                    # rollback למניעת דליפת אובייקטים מתחנה שנכשלה לתחנה הבאה
                    await db.rollback()

            logger.info(
                "סיום בדיקת חסימה אוטומטית",
                extra_data={
                    "stations_processed": len(stations),
                    "drivers_blocked": total_blocked,
                }
            )
            return {
                "stations_processed": len(stations),
                "drivers_blocked": total_blocked,
            }

    return run_async(_process())
