"""
Admin Notification Service - Notify admins about courier events
"""
import httpx
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.core.circuit_breaker import get_telegram_circuit_breaker

logger = get_logger(__name__)


class AdminNotificationService:
    """Service for sending notifications to admins"""

    @staticmethod
    async def notify_new_courier_registration(
        user_id: int,
        full_name: str,
        service_area: str,
        telegram_chat_id: str,
        document_file_id: Optional[str] = None
    ) -> bool:
        """
        Notify admin about new courier registration request.
        [1.4] Admin notification
        """
        if not settings.TELEGRAM_ADMIN_CHAT_ID or not settings.TELEGRAM_BOT_TOKEN:
            logger.warning(
                "Admin notification not configured",
                extra_data={"missing": "TELEGRAM_ADMIN_CHAT_ID or TELEGRAM_BOT_TOKEN"}
            )
            return False

        message = f"""
👤 <b>שליח חדש מבקש להירשם!</b>

📋 <b>פרטים:</b>
• שם מלא: {full_name}
• אזור: {service_area}
• Telegram ID: {telegram_chat_id}

📎 מסמך זהות: {'נשלח' if document_file_id else 'לא נשלח'}

לאישור השליח:
<code>/approve {user_id}</code>

לדחיית השליח:
<code>/reject {user_id}</code>
"""

        success = await AdminNotificationService._send_telegram_message(
            settings.TELEGRAM_ADMIN_CHAT_ID,
            message
        )

        # Forward document if exists
        if document_file_id and success:
            await AdminNotificationService._forward_photo(
                settings.TELEGRAM_ADMIN_CHAT_ID,
                document_file_id
            )

        return success

    @staticmethod
    async def notify_deposit_request(
        user_id: int,
        full_name: str,
        telegram_chat_id: str,
        screenshot_file_id: str
    ) -> bool:
        """Notify admin about deposit request"""
        if not settings.TELEGRAM_ADMIN_CHAT_ID or not settings.TELEGRAM_BOT_TOKEN:
            return False

        message = f"""
💳 <b>בקשת הפקדה חדשה!</b>

📋 <b>פרטי השליח:</b>
• שם: {full_name}
• Telegram ID: {telegram_chat_id}
• User ID: {user_id}

📸 צילום מסך העברה: נשלח

לאישור ההפקדה:
<code>/deposit {user_id} [סכום]</code>
"""

        success = await AdminNotificationService._send_telegram_message(
            settings.TELEGRAM_ADMIN_CHAT_ID,
            message
        )

        if success and screenshot_file_id:
            await AdminNotificationService._forward_photo(
                settings.TELEGRAM_ADMIN_CHAT_ID,
                screenshot_file_id
            )

        return success

    @staticmethod
    async def notify_courier_approved(telegram_chat_id: str) -> bool:
        """Notify courier that they've been approved"""
        if not settings.TELEGRAM_BOT_TOKEN:
            return False

        message = """
🎉 <b>חשבונך אושר!</b>

ברוכים הבאים למערכת השליחים!
מעכשיו תוכל לתפוס משלוחים ולהתחיל לעבוד.

כתוב "תפריט" כדי להתחיל.
"""

        return await AdminNotificationService._send_telegram_message(
            telegram_chat_id,
            message
        )

    @staticmethod
    async def _send_telegram_message(chat_id: str, text: str) -> bool:
        """Send a message via Telegram Bot API"""
        if not settings.TELEGRAM_BOT_TOKEN:
            return False

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        circuit_breaker = get_telegram_circuit_breaker()

        async def _send():
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=30.0)
                if response.status_code != 200:
                    raise Exception(f"Telegram API returned {response.status_code}")
                return True

        try:
            return await circuit_breaker.execute(_send)
        except Exception as e:
            logger.error(
                "Error sending admin Telegram message",
                extra_data={"chat_id": chat_id, "error": str(e)},
                exc_info=True
            )
            return False

    @staticmethod
    async def _forward_photo(chat_id: str, file_id: str) -> bool:
        """Send a photo via Telegram Bot API using file_id"""
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram bot token not configured for photo forwarding")
            return False

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendPhoto"

        payload = {
            "chat_id": chat_id,
            "photo": file_id,
        }

        circuit_breaker = get_telegram_circuit_breaker()

        async def _send():
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=30.0)
                if response.status_code != 200:
                    raise Exception(f"Telegram API returned {response.status_code}")
                return True

        try:
            return await circuit_breaker.execute(_send)
        except Exception as e:
            logger.error(
                "Error sending photo",
                extra_data={"chat_id": chat_id, "error": str(e)},
                exc_info=True
            )
            return False
