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


def _parse_csv_setting(value: str) -> list[str]:
    """פירוק הגדרת CSV למערך ערכים נקיים"""
    return [v.strip() for v in value.split(",") if v.strip()]


class AdminNotificationService:
    """Service for sending notifications to admins"""

    # מיפוי קטגוריות רכב לתצוגה בעברית
    VEHICLE_CATEGORY_DISPLAY = {
        "car_4": "רכב 4 מקומות",
        "car_7": "7 מקומות",
        "pickup_truck": "טנדר",
        "motorcycle": "אופנוע",
    }

    # ──────────────────────────────────────────────
    #  כרטיס נהג → שליחה לפרטי של מנהלים עם כפתורים
    # ──────────────────────────────────────────────

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
        שליחת "כרטיס נהג" למנהלים בפרטי לאישור [שלב 2].
        כולל כפתורי אישור/דחייה. הסיכום יישלח לקבוצה אחרי ההחלטה.
        """
        success = False

        vehicle_display = AdminNotificationService.VEHICLE_CATEGORY_DISPLAY.get(
            vehicle_category, vehicle_category or "לא צוין"
        )

        # --- שליחה למנהלים פרטיים בוואטסאפ ---
        wa_admin_numbers = _parse_csv_setting(settings.WHATSAPP_ADMIN_NUMBERS)
        # fallback: אם לא הוגדרו מנהלים פרטיים, שולח לקבוצה (תאימות לאחור)
        is_wa_fallback_to_group = not wa_admin_numbers
        wa_targets = wa_admin_numbers if wa_admin_numbers else (
            [settings.WHATSAPP_ADMIN_GROUP_ID] if settings.WHATSAPP_ADMIN_GROUP_ID else []
        )

        if wa_targets:
            is_whatsapp = platform == "whatsapp"
            has_wa_doc = document_file_id and is_whatsapp
            has_wa_selfie = selfie_file_id and is_whatsapp
            has_wa_vehicle = vehicle_photo_file_id and is_whatsapp

            doc_status = 'נשלח למטה ⬇️' if has_wa_doc else 'זמין בטלגרם' if document_file_id else 'לא נשלח'
            selfie_status = 'נשלח למטה ⬇️' if has_wa_selfie else 'זמין בטלגרם' if selfie_file_id else '✗'
            vehicle_status = 'נשלח למטה ⬇️' if has_wa_vehicle else 'זמין בטלגרם' if vehicle_photo_file_id else '✗'

            wa_message = f"""👤 *כרטיס נהג חדש #{user_id}*

📋 *פרטים:*
• שם: {full_name}
• אזור: {service_area}
• רכב: {vehicle_display}
• פלטפורמה: {platform}

📎 מסמכים:
  - ת.ז./רישיון: {doc_status}
  - סלפי: {selfie_status}
  - תמונת רכב: {vehicle_status}"""

            # כפתורים רק בצ'אט פרטי; בקבוצה - הנחיות טקסטואליות
            if is_wa_fallback_to_group:
                wa_message += f"""

✅ לאישור: *אשר {user_id}*
❌ לדחייה: *דחה {user_id}*"""
                wa_keyboard = None
            else:
                wa_keyboard = [[f"✅ אשר {user_id}", f"❌ דחה {user_id}"]]

            for target in wa_targets:
                wa_sent = await AdminNotificationService._send_whatsapp_admin_message(
                    target, wa_message, keyboard=wa_keyboard
                )
                success = success or wa_sent

                # שליחת תמונות (רק אם מוואטסאפ)
                if is_whatsapp and wa_sent:
                    for label, file_id in [
                        ("document", document_file_id),
                        ("selfie", selfie_file_id),
                        ("vehicle", vehicle_photo_file_id),
                    ]:
                        if not file_id:
                            continue
                        photo_sent = await AdminNotificationService._send_whatsapp_admin_photo(
                            target, file_id
                        )
                        if not photo_sent:
                            logger.warning(
                                f"Failed to send {label} photo to WhatsApp admin",
                                extra_data={"user_id": user_id, "target": target}
                            )

        # --- שליחה למנהלים פרטיים בטלגרם ---
        tg_admin_ids = _parse_csv_setting(settings.TELEGRAM_ADMIN_CHAT_IDS)
        # fallback: אם לא הוגדרו מנהלים פרטיים, שולח ל-ADMIN_CHAT_ID (תאימות לאחור)
        if not tg_admin_ids and settings.TELEGRAM_ADMIN_CHAT_ID:
            tg_admin_ids = [settings.TELEGRAM_ADMIN_CHAT_ID]

        if tg_admin_ids and settings.TELEGRAM_BOT_TOKEN:
            is_telegram = platform == "telegram"
            has_tg_doc = document_file_id and is_telegram
            has_tg_selfie = selfie_file_id and is_telegram
            has_tg_vehicle = vehicle_photo_file_id and is_telegram

            tg_doc_status = 'נשלח למטה ⬇️' if has_tg_doc else 'זמין בוואטסאפ' if document_file_id else 'לא נשלח'
            tg_selfie_status = 'נשלח למטה ⬇️' if has_tg_selfie else 'זמין בוואטסאפ' if selfie_file_id else '✗'
            tg_vehicle_status = 'נשלח למטה ⬇️' if has_tg_vehicle else 'זמין בוואטסאפ' if vehicle_photo_file_id else '✗'

            tg_message = f"""👤 <b>כרטיס נהג חדש #{user_id}</b>

📋 <b>פרטים:</b>
• שם: {full_name}
• אזור: {service_area}
• רכב: {vehicle_display}
• פלטפורמה: {platform}

📎 <b>מסמכים:</b>
  - ת.ז./רישיון: {tg_doc_status}
  - סלפי: {tg_selfie_status}
  - תמונת רכב: {tg_vehicle_status}"""

            # כפתורי inline לטלגרם
            inline_keyboard = [[
                {"text": "✅ אשר", "callback_data": f"approve_courier_{user_id}"},
                {"text": "❌ דחה", "callback_data": f"reject_courier_{user_id}"},
            ]]

            for admin_id in tg_admin_ids:
                tg_sent = await AdminNotificationService._send_telegram_message_with_inline_keyboard(
                    admin_id, tg_message, inline_keyboard
                )
                success = success or tg_sent

                # שליחת תמונות (רק אם מטלגרם)
                if is_telegram and tg_sent:
                    for file_id in [document_file_id, selfie_file_id, vehicle_photo_file_id]:
                        if file_id:
                            await AdminNotificationService._forward_photo(admin_id, file_id)

        if not success:
            logger.warning(
                "Admin notification not configured or failed",
                extra_data={"user_id": user_id}
            )

        return success

    # ──────────────────────────────────────────────
    #  סיכום אישור/דחייה → שליחה לקבוצת מנהלים
    # ──────────────────────────────────────────────

    @staticmethod
    async def notify_group_courier_decision(
        user_id: int,
        full_name: str,
        service_area: str,
        vehicle_category: Optional[str],
        platform: str,
        decision: str,
        decided_by: str,
    ) -> bool:
        """
        שליחת סיכום החלטת אישור/דחייה לקבוצת מנהלים.
        נקרא אחרי שמנהל לוחץ אשר/דחה בפרטי.
        """
        success = False

        vehicle_display = AdminNotificationService.VEHICLE_CATEGORY_DISPLAY.get(
            vehicle_category, vehicle_category or "לא צוין"
        )

        if decision == "approved":
            status_icon = "✅"
            status_text = "אושר"
        else:
            status_icon = "❌"
            status_text = "נדחה"

        # שליחה לקבוצת וואטסאפ
        if settings.WHATSAPP_ADMIN_GROUP_ID:
            wa_msg = f"""{status_icon} *כרטיס נהג #{user_id} - {status_text}*

📋 *פרטים:*
• שם: {full_name}
• אזור: {service_area}
• רכב: {vehicle_display}
• פלטפורמה: {platform}

📌 *סטטוס:* {status_text}
👤 *על ידי:* {decided_by}"""

            wa_success = await AdminNotificationService._send_whatsapp_admin_message(
                settings.WHATSAPP_ADMIN_GROUP_ID, wa_msg
            )
            success = success or wa_success

        # שליחה לקבוצת טלגרם
        if settings.TELEGRAM_ADMIN_CHAT_ID and settings.TELEGRAM_BOT_TOKEN:
            tg_msg = f"""{status_icon} <b>כרטיס נהג #{user_id} - {status_text}</b>

📋 <b>פרטים:</b>
• שם: {full_name}
• אזור: {service_area}
• רכב: {vehicle_display}
• פלטפורמה: {platform}

📌 <b>סטטוס:</b> {status_text}
👤 <b>על ידי:</b> {decided_by}"""

            tg_success = await AdminNotificationService._send_telegram_message(
                settings.TELEGRAM_ADMIN_CHAT_ID, tg_msg
            )
            success = success or tg_success

        return success

    # ──────────────────────────────────────────────
    #  הודעות אחרות (הפקדות, אישור שליח)
    # ──────────────────────────────────────────────

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

    # ──────────────────────────────────────────────
    #  שיטות עזר - טלגרם
    # ──────────────────────────────────────────────

    @staticmethod
    async def _send_telegram_message(chat_id: str, text: str) -> bool:
        """שליחת הודעת טקסט רגילה לטלגרם"""
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
    async def _send_telegram_message_with_inline_keyboard(
        chat_id: str,
        text: str,
        inline_keyboard: list[list[dict]],
    ) -> bool:
        """שליחת הודעה עם כפתורי inline לטלגרם"""
        if not settings.TELEGRAM_BOT_TOKEN:
            return False

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": inline_keyboard
            },
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
                "Error sending Telegram inline keyboard message",
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

    # ──────────────────────────────────────────────
    #  שיטות עזר - וואטסאפ
    # ──────────────────────────────────────────────

    @staticmethod
    async def _send_whatsapp_admin_message(
        phone_or_group: str,
        text: str,
        keyboard: list = None
    ) -> bool:
        """שליחת הודעה למנהל/קבוצה בוואטסאפ"""
        if not settings.WHATSAPP_GATEWAY_URL:
            logger.warning("WhatsApp gateway URL not configured")
            return False

        circuit_breaker = get_whatsapp_circuit_breaker()

        async def _send():
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{settings.WHATSAPP_GATEWAY_URL}/send",
                    json={
                        "phone": phone_or_group,
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
                extra_data={"target": phone_or_group, "error": str(e)},
                exc_info=True
            )
            return False

    @staticmethod
    async def _send_whatsapp_admin_photo(phone_or_group: str, media_url: str) -> bool:
        """שליחת תמונה למנהל/קבוצה בוואטסאפ"""
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
                        "phone": phone_or_group,
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
                extra_data={"target": phone_or_group, "error": str(e)},
                exc_info=True
            )
            return False
