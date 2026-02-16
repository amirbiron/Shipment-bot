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
from app.domain.services.whatsapp import get_whatsapp_provider, get_whatsapp_group_provider
from app.core.logging import get_logger, set_correlation_id
from app.core.circuit_breaker import get_telegram_circuit_breaker
from app.core.exceptions import TelegramError
from app.core.validation import PhoneNumberValidator
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
            # סגירת Redis singleton לפני סגירת ה-loop — מונע שימוש חוזר
            # ב-client שמחובר ל-event loop סגור בהרצה הבאה
            from app.core.redis_client import close_redis
            loop.run_until_complete(close_redis())
        except Exception as e:
            logger.warning(
                "כשלון בסגירת Redis בסיום task",
                extra_data={"error": str(e)},
            )
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
    שליחת הודעה דרך ספק WhatsApp הפעיל — ניתוב אוטומטי לפי סוג היעד.
    קבוצה (@g.us) → WPPConnect, פרטי → Cloud API (במצב hybrid) / WPPConnect.
    ממיר תגי HTML לפורמט הספק לפני שליחה.
    """
    message_text = content.get("message_text", "")
    if phone.endswith("@g.us"):
        provider = get_whatsapp_group_provider()
    else:
        provider = get_whatsapp_provider()
    formatted_text = provider.format_text(message_text)
    try:
        await provider.send_text(to=phone, text=formatted_text)
        return True
    except Exception as exc:
        logger.error(
            "WhatsApp send error",
            extra_data={"phone": PhoneNumberValidator.mask(phone), "error": str(exc)},
            exc_info=True,
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


@celery_app.task(name="app.workers.tasks.check_station_alerts")
def check_station_alerts():
    """
    בדיקה תקופתית — סף ארנק ומשלוחים שלא נאספו.

    רצה כל 5 דקות. לכל תחנה פעילה:
    1. בודקת אם יתרת הארנק מתחת לסף שהוגדר (Redis)
    2. בודקת אם יש משלוחים פתוחים שלא נאספו מעל 2 שעות
    משלחת התראות בזמן אמת לפאנל דרך Redis Pub/Sub.

    משתמשת ב-Redis SET NX EX למניעת התראות כפולות:
    - סף ארנק: throttle של שעה אחת לכל תחנה
    - משלוח לא נאסף: throttle של שעה אחת לכל משלוח
    """
    from app.db.models.station import Station
    from app.db.models.station_wallet import StationWallet
    from app.db.models.delivery import Delivery, DeliveryStatus
    from app.domain.services.alert_service import (
        get_wallet_threshold,
        publish_wallet_threshold_alert,
        publish_uncollected_shipment_alert,
        DEFAULT_UNCOLLECTED_HOURS,
    )
    from app.core.redis_client import get_redis

    # זמן throttle בשניות — התראה אחת לשעה לכל סוג/ישות
    _ALERT_THROTTLE_SECONDS = 3600

    async def _check():
        async with get_task_session() as db:
            # שליפת כל התחנות הפעילות
            result = await db.execute(
                select(Station).where(Station.is_active == True)  # noqa: E712
            )
            stations = list(result.scalars().all())

            # חילוץ מזהים לערכי Python רגילים — מונע MissingGreenlet
            # אחרי rollback (שעושה expire לאובייקטי ORM)
            station_ids = [s.id for s in stations]

            redis = await get_redis()
            alerts_sent = 0
            for station_id in station_ids:
                try:
                    # --- בדיקת סף ארנק ---
                    threshold = await get_wallet_threshold(station_id)
                    if threshold > 0:
                        wallet_result = await db.execute(
                            select(StationWallet).where(
                                StationWallet.station_id == station_id
                            )
                        )
                        wallet = wallet_result.scalar_one_or_none()
                        if wallet and float(wallet.balance) < threshold:
                            # dedupe — התראה אחת לשעה לכל תחנה
                            throttle_key = f"alert_throttle:wallet:{station_id}"
                            if await redis.set(
                                throttle_key, "1", nx=True, ex=_ALERT_THROTTLE_SECONDS
                            ):
                                await publish_wallet_threshold_alert(
                                    station_id=station_id,
                                    current_balance=float(wallet.balance),
                                    threshold=threshold,
                                )
                                alerts_sent += 1

                    # --- בדיקת משלוחים שלא נאספו ---
                    # datetime.utcnow() — naive, תואם לעמודת created_at (DateTime ללא timezone)
                    cutoff = datetime.utcnow() - timedelta(
                        hours=DEFAULT_UNCOLLECTED_HOURS
                    )
                    uncollected_result = await db.execute(
                        select(Delivery).where(
                            Delivery.station_id == station_id,
                            Delivery.status == DeliveryStatus.OPEN,
                            Delivery.created_at < cutoff,
                        )
                    )
                    uncollected = list(uncollected_result.scalars().all())
                    for d in uncollected:
                        # dedupe — התראה אחת לשעה לכל משלוח
                        throttle_key = f"alert_throttle:uncollected:{d.id}"
                        if not await redis.set(
                            throttle_key, "1", nx=True, ex=_ALERT_THROTTLE_SECONDS
                        ):
                            continue
                        # שני הצדדים naive — אין צורך ב-replace
                        hours_open = (
                            datetime.utcnow() - d.created_at
                        ).total_seconds() / 3600
                        await publish_uncollected_shipment_alert(
                            station_id=station_id,
                            delivery_id=d.id,
                            hours_open=hours_open,
                            pickup_address=d.pickup_address,
                        )
                        alerts_sent += 1

                except Exception as e:
                    logger.error(
                        "כשלון בבדיקת התראות לתחנה",
                        extra_data={
                            "station_id": station_id,
                            "error": str(e),
                        },
                        exc_info=True,
                    )
                    # rollback למניעת דליפת אובייקטים מתחנה שנכשלה לתחנה הבאה
                    await db.rollback()

            logger.info(
                "סיום בדיקת התראות תקופתית",
                extra_data={
                    "stations_checked": len(station_ids),
                    "alerts_sent": alerts_sent,
                },
            )
            return {
                "stations_checked": len(station_ids),
                "alerts_sent": alerts_sent,
            }

    return run_async(_check())


@celery_app.task(name="app.workers.tasks.process_billing_cycle_blocking")
def process_billing_cycle_blocking():
    """
    סעיף 10: בדיקה יומית — חסימה אוטומטית של נהגים שלא שילמו.

    רץ יומית (idempotent) — בודק כל תחנה פעילה עם חסימה אוטומטית מופעלת.
    מכבד הגדרות per-station: תקופת חסד (grace_months) וסף חוב מינימלי (min_debt).
    """
    from app.db.models.station import Station
    from app.domain.services.station_service import StationService

    async def _process():
        async with get_task_session() as db:
            # שליפת תחנות פעילות עם חסימה אוטומטית מופעלת
            result = await db.execute(
                select(Station).where(
                    Station.is_active == True,  # noqa: E712
                    Station.auto_block_enabled == True,  # noqa: E712
                )
            )
            stations = list(result.scalars().all())

            # חילוץ מזהים לערכי Python רגילים — מונע MissingGreenlet
            # אחרי rollback (שעושה expire לאובייקטי ORM)
            station_ids = [s.id for s in stations]

            total_blocked = 0
            for station_id in station_ids:
                try:
                    station_service = StationService(db)
                    blocked = await station_service.auto_block_unpaid_drivers(
                        station_id
                    )
                    total_blocked += len(blocked)
                except Exception as e:
                    logger.error(
                        "כשלון בבדיקת חסימה אוטומטית לתחנה",
                        extra_data={
                            "station_id": station_id,
                            "error": str(e),
                        },
                        exc_info=True,
                    )
                    # rollback למניעת דליפת אובייקטים מתחנה שנכשלה לתחנה הבאה
                    await db.rollback()

            logger.info(
                "סיום בדיקת חסימה אוטומטית",
                extra_data={
                    "stations_processed": len(station_ids),
                    "drivers_blocked": total_blocked,
                }
            )
            return {
                "stations_processed": len(station_ids),
                "drivers_blocked": total_blocked,
            }

    return run_async(_process())


@celery_app.task(name="app.workers.tasks.generate_monthly_reports")
def generate_monthly_reports():
    """
    סעיף 7: הפקת דוחות חודשיים אוטומטית.

    רץ ב-1 לכל חודש — מייצר דוח חודשי Excel לכל תחנה פעילה
    ושומר אותו מקודד ב-base64 ב-Redis לתקופה של 30 יום.
    בעל התחנה יכול להוריד את הדוח דרך endpoint ייעודי.
    """
    import base64
    import calendar
    from app.db.models.station import Station
    from app.domain.services.station_service import StationService
    from app.domain.services.export_service import generate_monthly_summary_excel
    from app.core.redis_client import get_redis

    async def _generate():
        async with get_task_session() as db:
            # חישוב חודש קודם
            now = datetime.utcnow()
            if now.month == 1:
                report_year, report_month = now.year - 1, 12
            else:
                report_year, report_month = now.year, now.month - 1

            dt_from = datetime(report_year, report_month, 1)
            last_day = calendar.monthrange(report_year, report_month)[1]
            dt_to = datetime(report_year, report_month, last_day, 23, 59, 59, 999999)
            month_str = f"{report_year}-{report_month:02d}"

            # שליפת כל התחנות הפעילות
            result = await db.execute(
                select(Station).where(Station.is_active == True)  # noqa: E712
            )
            stations = list(result.scalars().all())
            # חילוץ נתונים לערכי Python — מונע MissingGreenlet
            station_data = [(s.id, s.name) for s in stations]

            reports_generated = 0
            redis = await get_redis()

            for station_id, station_name in station_data:
                try:
                    station_service = StationService(db)

                    # נתוני הכנסות
                    pl_data = await station_service.get_profit_loss_report(
                        station_id, dt_from, dt_to
                    )
                    if pl_data:
                        revenue = pl_data[0]
                    else:
                        revenue = {
                            "commissions": 0.0,
                            "manual_charges": 0.0,
                            "withdrawals": 0.0,
                            "net": 0.0,
                        }

                    # סטטיסטיקות משלוחים
                    delivery_stats = await station_service.get_monthly_delivery_stats(
                        station_id, dt_from, dt_to
                    )

                    # נתוני גבייה
                    collection_data = await station_service.get_collection_report_for_period(
                        station_id, dt_from, dt_to
                    )
                    total_debt = sum(float(item["total_debt"]) for item in collection_data)

                    # יצירת Excel
                    xlsx_bytes = generate_monthly_summary_excel(
                        month=month_str,
                        station_name=station_name,
                        collection_items=collection_data,
                        total_debt=total_debt,
                        revenue_data=revenue,
                        delivery_stats=delivery_stats,
                    )

                    # שמירה ב-Redis למשך 30 יום
                    # הקידוד ל-base64 נדרש כי ה-Redis client מוגדר עם decode_responses=True
                    # ולכן לא יכול לאחסן/לשלוף bytes ישירות
                    cache_key = f"monthly_report:{station_id}:{month_str}"
                    encoded = base64.b64encode(xlsx_bytes).decode("ascii")
                    await redis.set(cache_key, encoded, ex=30 * 86400)

                    reports_generated += 1
                    logger.info(
                        "דוח חודשי הופק בהצלחה",
                        extra_data={
                            "station_id": station_id,
                            "month": month_str,
                        },
                    )
                except Exception as e:
                    logger.error(
                        "כשלון בהפקת דוח חודשי לתחנה",
                        extra_data={
                            "station_id": station_id,
                            "month": month_str,
                            "error": str(e),
                        },
                        exc_info=True,
                    )
                    await db.rollback()

            logger.info(
                "סיום הפקת דוחות חודשיים",
                extra_data={
                    "month": month_str,
                    "stations_total": len(station_data),
                    "reports_generated": reports_generated,
                },
            )
            return {
                "month": month_str,
                "stations_total": len(station_data),
                "reports_generated": reports_generated,
            }

    return run_async(_generate())


@celery_app.task(name="app.workers.tasks.check_whatsapp_connection")
def check_whatsapp_connection() -> dict:
    """
    בדיקת חיבור WhatsApp Gateway תקופתית.

    רצה כל 3 דקות. בודק שה-gateway פעיל ושה-session מחובר.
    אם ה-session מנותק (למשל אחרי OOM restart) — שולח התראה
    למנהלים דרך Telegram (כי WhatsApp לא זמין).
    משתמש ב-Redis throttling למניעת הצפת התראות (פעם ב-15 דקות).
    """
    import httpx
    from app.core.config import settings
    from app.core.redis_client import get_redis

    # throttle — התראה אחת ל-15 דקות
    _THROTTLE_KEY = "alert_throttle:whatsapp_disconnected"
    _THROTTLE_SECONDS = 900

    async def _check() -> dict:
        status = "unknown"
        alert_sent = False

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{settings.WHATSAPP_GATEWAY_URL}/health"
                )

            if response.status_code != 200:
                status = "gateway_error"
                logger.error(
                    "WhatsApp Gateway לא זמין — סטטוס HTTP לא תקין",
                    extra_data={"status_code": response.status_code},
                )
            else:
                data = response.json()
                if data.get("connected"):
                    status = "connected"
                    # אם מחובר — ננקה את ה-throttle key כדי שהתראה הבאה תשלח מיד
                    try:
                        redis = await get_redis()
                        await redis.delete(_THROTTLE_KEY)
                    except Exception:
                        pass
                    logger.debug("WhatsApp Gateway מחובר ותקין")
                    return {"status": status, "alert_sent": False}
                else:
                    status = "disconnected"
                    logger.error(
                        "WhatsApp Gateway פעיל אך ה-session מנותק!",
                        extra_data={"response": data},
                    )
        except httpx.TimeoutException:
            status = "timeout"
            logger.error("WhatsApp Gateway — timeout בבדיקת חיבור")
        except httpx.RequestError as exc:
            status = "unreachable"
            logger.error(
                "WhatsApp Gateway — לא ניתן להתחבר",
                extra_data={"error": str(exc)},
            )
        except Exception as exc:
            status = "error"
            logger.error(
                "WhatsApp Gateway — שגיאה לא צפויה בבדיקת חיבור",
                extra_data={"error": str(exc)},
                exc_info=True,
            )

        # ── שליחת התראה למנהלים דרך Telegram ──
        try:
            redis = await get_redis()
            # throttle — אם כבר שלחנו התראה ב-15 הדקות האחרונות, לא שולחים שוב
            was_set = await redis.set(
                _THROTTLE_KEY, "1", nx=True, ex=_THROTTLE_SECONDS
            )
            if not was_set:
                logger.info(
                    "WhatsApp disconnected alert throttled",
                    extra_data={"status": status},
                )
                return {"status": status, "alert_sent": False}
        except Exception as exc:
            logger.warning(
                "כשלון בבדיקת throttle ב-Redis — ממשיך לשלוח התראה",
                extra_data={"error": str(exc)},
            )

        # שליחה דרך Telegram בלבד (WhatsApp לא זמין)
        alert_message = (
            f"🔴 <b>התראת WhatsApp Gateway</b>\n\n"
            f"סטטוס: <code>{status}</code>\n"
            f"Gateway URL: <code>{settings.WHATSAPP_GATEWAY_URL}</code>\n\n"
            f"ה-WhatsApp Gateway לא מחובר.\n"
            f"הודעות WhatsApp לא נשלחות!\n\n"
            f"יש לבדוק את ה-service ולעשות restart אם צריך."
        )

        # שליחה לכל מנהלי Telegram (פרטי + קבוצה)
        from app.domain.services.admin_notification_service import (
            AdminNotificationService,
            _parse_csv_setting,
        )

        tg_targets: list[str] = []
        tg_admin_ids = _parse_csv_setting(settings.TELEGRAM_ADMIN_CHAT_IDS)
        tg_targets.extend(tg_admin_ids)
        if settings.TELEGRAM_ADMIN_CHAT_ID and settings.TELEGRAM_ADMIN_CHAT_ID not in tg_targets:
            tg_targets.append(settings.TELEGRAM_ADMIN_CHAT_ID)

        for target in tg_targets:
            sent = await AdminNotificationService._send_telegram_message(
                target, alert_message
            )
            if sent:
                alert_sent = True
                logger.info(
                    "התראת WhatsApp disconnected נשלחה למנהל",
                    extra_data={"target": target, "status": status},
                )

        if not alert_sent and tg_targets:
            logger.error(
                "כשלון בשליחת התראת WhatsApp disconnected לכל המנהלים",
                extra_data={"status": status, "targets_count": len(tg_targets)},
            )

        return {"status": status, "alert_sent": alert_sent}

    return run_async(_check())
