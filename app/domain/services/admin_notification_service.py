"""
Admin Notification Service - Notify admins about courier events
"""
import httpx
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.core.circuit_breaker import get_telegram_circuit_breaker, get_whatsapp_circuit_breaker

logger = get_logger(__name__)


class AdminNotificationService:
    """Service for sending notifications to admins"""

    @staticmethod
    async def notify_new_courier_registration(
        user_id: int,
        full_name: str,
        service_area: str,
        phone_or_chat_id: str,
        document_file_id: Optional[str] = None,
        platform: str = "telegram"
    ) -> bool:
        """
        Notify admin about new courier registration request.
        [1.4] Admin notification - שולח לטלגרם ו/או וואטסאפ לפי מה שמוגדר
        """
        success = False

        # שליחה לקבוצת וואטסאפ (אם מוגדר)
        if settings.WHATSAPP_ADMIN_GROUP_ID:
            # הערה: document_file_id הוא platform-specific
            # תמונה מוואטסאפ תישלח רק לקבוצת וואטסאפ
            has_whatsapp_photo = document_file_id and platform == "whatsapp"
            whatsapp_message = f"""
👤 *שליח חדש מבקש להירשם!*

📋 *פרטים:*
• שם מלא: {full_name}
• אזור: {service_area}
• מזהה: {phone_or_chat_id}
• פלטפורמה: {platform}

📎 מסמך זהות: {'נשלח (ראה למטה)' if has_whatsapp_photo else 'נשלח (זמין בטלגרם)' if document_file_id else 'לא נשלח'}
"""
            whatsapp_success = await AdminNotificationService._send_whatsapp_admin_message(
                settings.WHATSAPP_ADMIN_GROUP_ID,
                whatsapp_message,
                keyboard=[
                    [f"✅ אשר שליח {user_id}", f"❌ דחה שליח {user_id}"]
                ]
            )
            success = success or whatsapp_success

            # שליחת התמונה לוואטסאפ רק אם היא מוואטסאפ
            if has_whatsapp_photo and whatsapp_success:
                await AdminNotificationService._send_whatsapp_admin_photo(
                    settings.WHATSAPP_ADMIN_GROUP_ID,
                    document_file_id
                )

        # שליחה לטלגרם (אם מוגדר)
        if settings.TELEGRAM_ADMIN_CHAT_ID and settings.TELEGRAM_BOT_TOKEN:
            # תמונה מטלגרם תישלח רק לקבוצת טלגרם
            has_telegram_photo = document_file_id and platform == "telegram"
            telegram_message = f"""
👤 <b>שליח חדש מבקש להירשם!</b>

📋 <b>פרטים:</b>
• שם מלא: {full_name}
• אזור: {service_area}
• מזהה: {phone_or_chat_id}
• פלטפורמה: {platform}

📎 מסמך זהות: {'נשלח (ראה למטה)' if has_telegram_photo else 'נשלח (זמין בוואטסאפ)' if document_file_id else 'לא נשלח'}

לאישור השליח:
<code>/approve {user_id}</code>

לדחיית השליח:
<code>/reject {user_id}</code>
"""
            telegram_success = await AdminNotificationService._send_telegram_message(
                settings.TELEGRAM_ADMIN_CHAT_ID,
                telegram_message
            )
            success = success or telegram_success

            # שליחת התמונה לטלגרם רק אם היא מטלגרם
            if has_telegram_photo and telegram_success:
                await AdminNotificationService._forward_photo(
                    settings.TELEGRAM_ADMIN_CHAT_ID,
                    document_file_id
                )

        if not success:
            logger.warning(
                "Admin notification not configured or failed",
                extra_data={"user_id": user_id}
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

    @staticmethod
    async def _send_whatsapp_admin_message(
        group_id: str,
        text: str,
        keyboard: list = None
    ) -> bool:
        """שליחת הודעה לקבוצת מנהלים בוואטסאפ"""
        if not settings.WHATSAPP_GATEWAY_URL:
            logger.warning("WhatsApp gateway URL not configured")
            return False

        circuit_breaker = get_whatsapp_circuit_breaker()

        async def _send():
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{settings.WHATSAPP_GATEWAY_URL}/send",
                    json={
                        "phone": group_id,
                        "message": text,
                        "keyboard": keyboard
                    },
                    timeout=30.0
                )
                if response.status_code != 200:
                    raise Exception(f"WhatsApp API returned {response.status_code}")
                return True

        try:
            return await circuit_breaker.execute(_send)
        except Exception as e:
            logger.error(
                "Error sending WhatsApp admin message",
                extra_data={"group_id": group_id, "error": str(e)},
                exc_info=True
            )
            return False

    @staticmethod
    async def _send_whatsapp_admin_photo(group_id: str, media_url: str) -> bool:
        """שליחת תמונה לקבוצת מנהלים בוואטסאפ"""
        if not settings.WHATSAPP_GATEWAY_URL:
            return False

        circuit_breaker = get_whatsapp_circuit_breaker()

        async def _send():
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{settings.WHATSAPP_GATEWAY_URL}/send-media",
                    json={
                        "phone": group_id,
                        "media_url": media_url,
                        "media_type": "image"
                    },
                    timeout=30.0
                )
                if response.status_code != 200:
                    raise Exception(f"WhatsApp API returned {response.status_code}")
                return True

        try:
            return await circuit_breaker.execute(_send)
        except Exception as e:
            logger.error(
                "Error sending WhatsApp admin photo",
                extra_data={"group_id": group_id, "error": str(e)},
                exc_info=True
            )
            return False
