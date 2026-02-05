"""
Admin Notification Service - Notify admins about courier events
"""
import httpx
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.core.circuit_breaker import get_telegram_circuit_breaker, get_whatsapp_circuit_breaker
from app.core.exceptions import TelegramError, WhatsAppError

logger = get_logger(__name__)


class AdminNotificationService:
    """Service for sending notifications to admins"""

    # מיפוי קטגוריות רכב לתצוגה בעברית
    VEHICLE_CATEGORY_DISPLAY = {
        "car_4": "רכב 4 מקומות",
        "car_7": "7 מקומות",
        "pickup_truck": "טנדר",
        "motorcycle": "אופנוע",
    }

    @staticmethod
    async def notify_new_courier_registration(
        user_id: int,
        full_name: str,
        service_area: str,
        phone_or_chat_id: str,
        document_file_id: Optional[str] = None,
        platform: str = "telegram",
        vehicle_category: Optional[str] = None,
        selfie_file_id: Optional[str] = None,
        vehicle_photo_file_id: Optional[str] = None,
    ) -> bool:
        """
        שליחת "כרטיס נהג" למנהלים לאישור [שלב 2].
        כולל את כל הנתונים שנאספו בתהליך ה-KYC.
        """
        success = False

        # תרגום קטגוריית רכב לתצוגה
        vehicle_display = AdminNotificationService.VEHICLE_CATEGORY_DISPLAY.get(
            vehicle_category, vehicle_category or "לא צוין"
        )

        # שליחה לקבוצת וואטסאפ (אם מוגדר)
        if settings.WHATSAPP_ADMIN_GROUP_ID:
            is_whatsapp = platform == "whatsapp"
            has_whatsapp_doc = document_file_id and is_whatsapp
            has_whatsapp_selfie = selfie_file_id and is_whatsapp
            has_whatsapp_vehicle = vehicle_photo_file_id and is_whatsapp

            # תצוגת סטטוס מסמכים - מציגים ⬇️ אם התמונה תישלח למטה
            doc_status = 'נשלח למטה ⬇️' if has_whatsapp_doc else 'זמין בטלגרם' if document_file_id else 'לא נשלח'
            selfie_status = 'נשלח למטה ⬇️' if has_whatsapp_selfie else 'זמין בטלגרם' if selfie_file_id else '✗'
            vehicle_status = 'נשלח למטה ⬇️' if has_whatsapp_vehicle else 'זמין בטלגרם' if vehicle_photo_file_id else '✗'

            whatsapp_message = f"""👤 *כרטיס נהג חדש #{user_id}*

📋 *פרטים:*
• שם: {full_name}
• אזור: {service_area}
• רכב: {vehicle_display}
• פלטפורמה: {platform}

📎 מסמכים:
  - ת.ז./רישיון: {doc_status}
  - סלפי: {selfie_status}
  - תמונת רכב: {vehicle_status}

✅ לאישור: *אשר {user_id}*
❌ לדחייה: *דחה {user_id}*"""
            whatsapp_success = await AdminNotificationService._send_whatsapp_admin_message(
                settings.WHATSAPP_ADMIN_GROUP_ID,
                whatsapp_message,
                keyboard=None
            )
            success = success or whatsapp_success

            # שליחת כל התמונות לוואטסאפ (רק אם הן מוואטסאפ)
            if is_whatsapp and whatsapp_success:
                for label, file_id in [
                    ("document", document_file_id),
                    ("selfie", selfie_file_id),
                    ("vehicle", vehicle_photo_file_id),
                ]:
                    if not file_id:
                        continue
                    photo_sent = await AdminNotificationService._send_whatsapp_admin_photo(
                        settings.WHATSAPP_ADMIN_GROUP_ID,
                        file_id
                    )
                    if not photo_sent:
                        logger.warning(
                            f"Failed to send {label} photo to WhatsApp admin group",
                            extra_data={"user_id": user_id}
                        )

        # שליחה לטלגרם (אם מוגדר)
        if settings.TELEGRAM_ADMIN_CHAT_ID and settings.TELEGRAM_BOT_TOKEN:
            is_telegram = platform == "telegram"
            has_tg_doc = document_file_id and is_telegram
            has_tg_selfie = selfie_file_id and is_telegram
            has_tg_vehicle = vehicle_photo_file_id and is_telegram

            # תצוגת סטטוס מסמכים - מציגים ⬇️ אם התמונה תישלח למטה
            tg_doc_status = 'נשלח למטה ⬇️' if has_tg_doc else 'זמין בוואטסאפ' if document_file_id else 'לא נשלח'
            tg_selfie_status = 'נשלח למטה ⬇️' if has_tg_selfie else 'זמין בוואטסאפ' if selfie_file_id else '✗'
            tg_vehicle_status = 'נשלח למטה ⬇️' if has_tg_vehicle else 'זמין בוואטסאפ' if vehicle_photo_file_id else '✗'

            telegram_message = f"""👤 <b>כרטיס נהג חדש #{user_id}</b>

📋 <b>פרטים:</b>
• שם: {full_name}
• אזור: {service_area}
• רכב: {vehicle_display}
• פלטפורמה: {platform}

📎 <b>מסמכים:</b>
  - ת.ז./רישיון: {tg_doc_status}
  - סלפי: {tg_selfie_status}
  - תמונת רכב: {tg_vehicle_status}

✅ לאישור: <code>/approve {user_id}</code>
❌ לדחייה: <code>/reject {user_id}</code>"""
            telegram_success = await AdminNotificationService._send_telegram_message(
                settings.TELEGRAM_ADMIN_CHAT_ID,
                telegram_message
            )
            success = success or telegram_success

            # שליחת כל התמונות לטלגרם (רק אם הן מטלגרם)
            if is_telegram and telegram_success:
                for file_id in [document_file_id, selfie_file_id, vehicle_photo_file_id]:
                    if file_id:
                        await AdminNotificationService._forward_photo(
                            settings.TELEGRAM_ADMIN_CHAT_ID,
                            file_id
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
                    raise TelegramError.from_response(
                        "sendPhoto",
                        response,
                        message=f"sendPhoto returned status {response.status_code}",
                    )
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
                "Error sending WhatsApp admin message",
                extra_data={"group_id": group_id, "error": str(e)},
                exc_info=True
            )
            return False

    @staticmethod
    async def _send_whatsapp_admin_photo(group_id: str, media_url: str) -> bool:
        """שליחת תמונה לקבוצת מנהלים בוואטסאפ"""
        if not settings.WHATSAPP_GATEWAY_URL:
            logger.warning("WhatsApp gateway URL not configured for photo sending")
            return False

        if not media_url:
            logger.warning("No media_url provided for WhatsApp admin photo")
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
                    raise WhatsAppError.from_response(
                        "send-media",
                        response,
                        message=f"gateway /send-media returned status {response.status_code}",
                    )
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
