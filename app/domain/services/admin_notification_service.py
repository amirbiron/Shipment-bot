"""
Admin Notification Service - Notify admins about courier events
"""
import httpx
from typing import Optional

from app.core.config import settings


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
            print("Warning: Admin notification not configured")
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

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=30.0)
                return response.status_code == 200
        except Exception as e:
            print(f"Error sending Telegram message: {e}")
            return False

    @staticmethod
    async def _forward_photo(chat_id: str, file_id: str) -> bool:
        """Send a photo via Telegram Bot API using file_id"""
        if not settings.TELEGRAM_BOT_TOKEN:
            return False

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendPhoto"

        payload = {
            "chat_id": chat_id,
            "photo": file_id,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=30.0)
                return response.status_code == 200
        except Exception as e:
            print(f"Error sending photo: {e}")
            return False
