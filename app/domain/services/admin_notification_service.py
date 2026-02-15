"""
Admin Notification Service - Notify admins about courier events
"""
import base64
import mimetypes
import httpx
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.core.circuit_breaker import get_telegram_circuit_breaker, get_whatsapp_cloud_circuit_breaker
from app.core.exceptions import TelegramError
from app.core.validation import PhoneNumberValidator, TextSanitizer
from app.domain.services.whatsapp import get_whatsapp_admin_provider, get_whatsapp_group_provider

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
            file_ids: dict[str, Optional[str]] = {
                "document": document_file_id,
                "selfie": selfie_file_id,
                "vehicle": vehicle_photo_file_id,
            }
            resolved_media_by_label: dict[str, Optional[str]] = {}
            wa_media_payloads: list[tuple[str, str]] = []

            for label, file_id in file_ids.items():
                if not file_id:
                    resolved_media_by_label[label] = None
                    continue

                should_attempt = True
                if (
                    platform == "telegram"
                    and not settings.TELEGRAM_BOT_TOKEN
                    and not AdminNotificationService._is_media_url(file_id)
                ):
                    should_attempt = False

                resolved_media = None
                if should_attempt:
                    resolved_media = await AdminNotificationService._resolve_whatsapp_media_url(
                        file_id=file_id,
                        platform=platform,
                    )

                resolved_media_by_label[label] = resolved_media
                if resolved_media:
                    wa_media_payloads.append((label, resolved_media))
                elif should_attempt:
                    logger.warning(
                        "Failed to prepare WhatsApp media payload",
                        extra_data={"user_id": user_id, "label": label},
                    )

            def _status(label: str, missing_value: str) -> str:
                file_id = file_ids.get(label)
                if resolved_media_by_label.get(label):
                    return 'נשלח למטה ⬇️'
                if not file_id:
                    return missing_value
                if platform == "telegram":
                    return 'זמין בטלגרם'
                return 'לא נשלח'

            doc_status = _status("document", "לא נשלח")
            selfie_status = _status("selfie", "✗")
            vehicle_status = _status("vehicle", "✗")

            # קישור יצירת קשר - לינק לפרופיל בטלגרם או מספר טלפון בוואטסאפ
            if platform == "telegram":
                wa_contact_line = f"טלגרם ID: {phone_or_chat_id}"
            else:
                wa_contact_line = phone_or_chat_id

            wa_message = f"""👤 *כרטיס נהג חדש #{user_id}*

📋 *פרטים:*
• שם: {full_name}
• אזור: {service_area}
• רכב: {vehicle_display}
• פלטפורמה: {platform}
• ליצירת קשר: {wa_contact_line}

📎 מסמכים:
  - ת.ז./רישיון: {doc_status}
  - סלפי: {selfie_status}
  - תמונת רכב: {vehicle_status}"""

            # הנחיות טקסטואליות תמיד - הכפתורים לא תמיד מרונדרים ב-WhatsApp
            wa_message += f"""

✅ לאישור: *אשר {user_id}*
❌ לדחייה: *דחה {user_id}*"""

            # כפתורים רק בצ'אט פרטי (בקבוצה לא עובדים)
            if is_wa_fallback_to_group:
                wa_keyboard = None
            else:
                wa_keyboard = [[f"✅ אשר {user_id}", f"❌ דחה {user_id}"]]

            for target in wa_targets:
                wa_sent = await AdminNotificationService._send_whatsapp_admin_message(
                    target, wa_message, keyboard=wa_keyboard
                )
                # fallback: אם נכשל עם כפתורים, ננסה בלי
                if not wa_sent and wa_keyboard:
                    logger.warning(
                        "WhatsApp admin message with keyboard failed, retrying without",
                        extra_data={"user_id": user_id, "target": target}
                    )
                    wa_sent = await AdminNotificationService._send_whatsapp_admin_message(
                        target, wa_message, keyboard=None
                    )
                success = success or wa_sent

                # שליחת תמונות (כולל מסמכי Telegram) - שולחים גם אם ההודעה הטקסטית נכשלה
                for label, media_url in wa_media_payloads:
                    photo_sent = await AdminNotificationService._send_whatsapp_admin_photo(
                        target, media_url
                    )
                    if photo_sent:
                        success = True
                    else:
                        logger.warning(
                            f"Failed to send {label} photo to WhatsApp admin",
                            extra_data={"user_id": user_id, "target": target},
                        )

        # --- שליחה למנהלים פרטיים בטלגרם ---
        tg_admin_ids = _parse_csv_setting(settings.TELEGRAM_ADMIN_CHAT_IDS)
        # fallback: אם לא הוגדרו מנהלים פרטיים, שולח ל-ADMIN_CHAT_ID (תאימות לאחור)
        is_tg_fallback_to_group = not tg_admin_ids
        if is_tg_fallback_to_group and settings.TELEGRAM_ADMIN_CHAT_ID:
            tg_admin_ids = [settings.TELEGRAM_ADMIN_CHAT_ID]

        if tg_admin_ids and settings.TELEGRAM_BOT_TOKEN:
            is_telegram = platform == "telegram"
            has_tg_doc = document_file_id and is_telegram
            has_tg_selfie = selfie_file_id and is_telegram
            has_tg_vehicle = vehicle_photo_file_id and is_telegram

            tg_doc_status = 'נשלח למטה ⬇️' if has_tg_doc else 'זמין בוואטסאפ' if document_file_id else 'לא נשלח'
            tg_selfie_status = 'נשלח למטה ⬇️' if has_tg_selfie else 'זמין בוואטסאפ' if selfie_file_id else '✗'
            tg_vehicle_status = 'נשלח למטה ⬇️' if has_tg_vehicle else 'זמין בוואטסאפ' if vehicle_photo_file_id else '✗'

            # קישור יצירת קשר - לינק לפרופיל בטלגרם או מספר טלפון בוואטסאפ
            if platform == "telegram":
                contact_line = f'<a href="tg://user?id={phone_or_chat_id}">פתח צ\'אט בטלגרם</a> (ID: {phone_or_chat_id})'
            else:
                contact_line = phone_or_chat_id

            tg_message = f"""👤 <b>כרטיס נהג חדש #{user_id}</b>

📋 <b>פרטים:</b>
• שם: {full_name}
• אזור: {service_area}
• רכב: {vehicle_display}
• פלטפורמה: {platform}
• ליצירת קשר: {contact_line}

📎 <b>מסמכים:</b>
  - ת.ז./רישיון: {tg_doc_status}
  - סלפי: {tg_selfie_status}
  - תמונת רכב: {tg_vehicle_status}"""

            # כפתורי inline רק בצ'אט פרטי; בקבוצה - הנחיות טקסטואליות
            # (כפתורי inline לא עובדים בקבוצה כי בדיקת ההרשאה
            #  מזהה לפי from_user.id שלא תואם ל-group ID)
            if is_tg_fallback_to_group:
                tg_message += f"""

✅ לאישור: <code>אשר {user_id}</code>
❌ לדחייה: <code>דחה {user_id}</code>"""
                inline_keyboard = None
            else:
                inline_keyboard = [[
                    {"text": "✅ אשר", "callback_data": f"approve_courier_{user_id}"},
                    {"text": "❌ דחה", "callback_data": f"reject_courier_{user_id}"},
                ]]

            for admin_id in tg_admin_ids:
                if inline_keyboard:
                    tg_sent = await AdminNotificationService._send_telegram_message_with_inline_keyboard(
                        admin_id, tg_message, inline_keyboard
                    )
                else:
                    tg_sent = await AdminNotificationService._send_telegram_message(
                        admin_id, tg_message
                    )
                success = success or tg_sent

                # שליחת תמונות (רק אם מטלגרם) - גם אם הודעת הטקסט נכשלה
                if is_telegram:
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
        rejection_note: Optional[str] = None,
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

        # שורת הערת דחייה (אם קיימת) — פורמט מרוכז דרך TextSanitizer
        wa_note_line = TextSanitizer.format_note_line(rejection_note, platform="whatsapp", label="הערה")
        tg_note_line = TextSanitizer.format_note_line(rejection_note, platform="telegram", label="הערה")

        # שליחה לקבוצת וואטסאפ
        if settings.WHATSAPP_ADMIN_GROUP_ID:
            wa_msg = f"""{status_icon} *כרטיס נהג #{user_id} - {status_text}*

📋 *פרטים:*
• שם: {full_name}
• אזור: {service_area}
• רכב: {vehicle_display}
• פלטפורמה: {platform}

📌 *סטטוס:* {status_text}{wa_note_line}
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

📌 <b>סטטוס:</b> {status_text}{tg_note_line}
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
        contact_identifier: str,
        screenshot_file_id: str,
        platform: str = "telegram",
    ) -> bool:
        """Notify admin about deposit request.

        contact_identifier — Telegram chat ID או מספר WhatsApp, בהתאם לפלטפורמה.
        """
        if not settings.TELEGRAM_ADMIN_CHAT_ID or not settings.TELEGRAM_BOT_TOKEN:
            return False

        # תווית ליצירת קשר לפי פלטפורמה
        if platform == "telegram":
            contact_line = f"Telegram ID: {contact_identifier}"
        else:
            contact_line = f"WhatsApp: {contact_identifier}"

        message = f"""
💳 <b>בקשת הפקדה חדשה!</b>

📋 <b>פרטי השליח:</b>
• שם: {full_name}
• {contact_line}
• User ID: {user_id}
• פלטפורמה: {platform}

📸 צילום מסך העברה: נשלח

לאישור ההפקדה:
<code>/deposit {user_id} [סכום]</code>
"""

        success = await AdminNotificationService._send_telegram_message(
            settings.TELEGRAM_ADMIN_CHAT_ID,
            message
        )

        if success and screenshot_file_id:
            if platform == "telegram":
                await AdminNotificationService._forward_photo(
                    settings.TELEGRAM_ADMIN_CHAT_ID,
                    screenshot_file_id
                )
            else:
                await AdminNotificationService._forward_whatsapp_photo_to_telegram(
                    settings.TELEGRAM_ADMIN_CHAT_ID,
                    screenshot_file_id,
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
    def _is_media_url(value: str) -> bool:
        return value.startswith("data:") or value.startswith("http://") or value.startswith("https://")

    @staticmethod
    def _pick_mime_type(file_path: str, content_type: str | None) -> str:
        if content_type:
            content_type = content_type.split(";")[0].strip()
            if content_type:
                return content_type
        guessed = mimetypes.guess_type(file_path)[0]
        return guessed or "application/octet-stream"

    @staticmethod
    async def _download_telegram_file_as_data_url(file_id: str) -> Optional[str]:
        """
        הורדת קובץ מטלגרם והמרה ל-data URL עבור שליחה בוואטסאפ.
        """
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning(
                "Telegram bot token not configured for media download",
                extra_data={"file_id": file_id},
            )
            return None

        base_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
        circuit_breaker = get_telegram_circuit_breaker()

        async def _fetch() -> tuple[str, bytes, str | None]:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{base_url}/getFile",
                    json={"file_id": file_id},
                    timeout=30.0,
                )
                if response.status_code != 200:
                    raise TelegramError.from_response(
                        "getFile",
                        response,
                        message=f"getFile returned status {response.status_code}",
                    )

                payload = response.json()
                if not payload.get("ok") or not payload.get("result"):
                    raise TelegramError(
                        "getFile returned ok=false",
                        details={"file_id": file_id, "response": payload},
                    )

                file_path = payload["result"].get("file_path")
                if not file_path:
                    raise TelegramError(
                        "getFile missing file_path",
                        details={"file_id": file_id, "response": payload},
                    )

                file_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"
                file_response = await client.get(file_url, timeout=30.0)
                if file_response.status_code != 200:
                    raise TelegramError.from_response(
                        "downloadFile",
                        file_response,
                        message=f"downloadFile returned status {file_response.status_code}",
                    )
                return file_path, file_response.content, file_response.headers.get("content-type")

        try:
            file_path, content, content_type = await circuit_breaker.execute(_fetch)
        except Exception as e:
            logger.error(
                "Failed to download Telegram file for WhatsApp forwarding",
                extra_data={"file_id": file_id, "error": str(e)},
                exc_info=True,
            )
            return None

        mime_type = AdminNotificationService._pick_mime_type(file_path, content_type)
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    async def _download_cloud_api_media_as_data_url(media_id: str) -> Optional[str]:
        """
        הורדת מדיה מ-WhatsApp Cloud API והמרה ל-data URL.

        Cloud API media IDs הם טוקנים זמניים של Meta — לא ניתן לשלוח אותם
        ישירות דרך send_image. צריך לפנות ל-API, לקבל URL להורדה,
        להוריד את התוכן ולהמיר ל-data URI.
        """
        token = settings.WHATSAPP_CLOUD_API_TOKEN
        if not token:
            logger.warning(
                "Cloud API token לא מוגדר — לא ניתן להוריד מדיה",
                extra_data={"media_id": media_id[:8] + "..." if len(media_id) > 8 else media_id},
            )
            return None

        circuit_breaker = get_whatsapp_cloud_circuit_breaker()

        async def _fetch() -> tuple[bytes, str | None]:
            async with httpx.AsyncClient() as client:
                # שלב 1: קבלת URL להורדה מ-Meta
                meta_resp = await client.get(
                    f"https://graph.facebook.com/v21.0/{media_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15.0,
                )
                if meta_resp.status_code != 200:
                    logger.error(
                        "Cloud API getMedia נכשל",
                        extra_data={
                            "media_id": media_id[:8] + "...",
                            "status": meta_resp.status_code,
                        },
                    )
                    return b"", None

                download_url = meta_resp.json().get("url")
                if not download_url:
                    logger.error(
                        "Cloud API getMedia — חסר URL להורדה",
                        extra_data={"media_id": media_id[:8] + "..."},
                    )
                    return b"", None

                # שלב 2: הורדת התוכן (דורש אותו Bearer token)
                content_resp = await client.get(
                    download_url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30.0,
                )
                if content_resp.status_code != 200:
                    logger.error(
                        "Cloud API media download נכשל",
                        extra_data={
                            "media_id": media_id[:8] + "...",
                            "status": content_resp.status_code,
                        },
                    )
                    return b"", None

                return content_resp.content, content_resp.headers.get("content-type")

        try:
            content, content_type = await circuit_breaker.execute(_fetch)
        except Exception as e:
            logger.error(
                "כשלון בהורדת מדיה מ-Cloud API",
                extra_data={"media_id": media_id[:8] + "...", "error": str(e)},
                exc_info=True,
            )
            return None

        if not content:
            return None

        mime_type = (content_type or "image/jpeg").split(";")[0].strip()
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    async def _resolve_whatsapp_media_url(
        file_id: str,
        platform: str,
    ) -> Optional[str]:
        """
        הכנת media_url מתאים לשליחה בוואטסאפ עבור WhatsApp/Telegram.

        עבור WhatsApp: אם file_id הוא URL/data URI — מחזיר כמו שהוא.
        אם הוא media_id של Cloud API — מוריד ומחזיר כ-data URI.
        עבור Telegram: מוריד מ-Telegram API ומחזיר כ-data URI.
        """
        if not file_id:
            return None
        if AdminNotificationService._is_media_url(file_id):
            return file_id
        if platform == "whatsapp":
            # file_id שהוא לא URL בפלטפורמת WhatsApp — מניחים שזה media_id של Cloud API.
            # WPPConnect תמיד מספק URLs (http://...) שנתפסים מעלה ב-_is_media_url.
            logger.debug(
                "WhatsApp non-URL file_id — מנסה להוריד כ-Cloud API media ID",
                extra_data={"file_id_prefix": file_id[:8] + "..." if len(file_id) > 8 else file_id},
            )
            return await AdminNotificationService._download_cloud_api_media_as_data_url(file_id)
        return await AdminNotificationService._download_telegram_file_as_data_url(file_id)

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
        """
        שליחת תמונה דרך Telegram Bot API לפי file_id.
        מנסה קודם sendPhoto; אם נכשל (למשל file_id ממסמך) — fallback ל-sendDocument.
        ניסיון sendPhoto נעשה בלי circuit breaker כי כשלון צפוי (file_id ממסמך)
        לא צריך להשפיע על ה-circuit breaker המשותף.
        אם ה-CB כבר פתוח — fast-fail (מחזיר False מיד, לא מנסה בכלל).
        """
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram bot token not configured for photo forwarding")
            return False

        base_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
        circuit_breaker = get_telegram_circuit_breaker()

        # בדיקה חד-פעמית אם ה-CB מאפשר קריאות.
        # שומרים את התוצאה כדי לא לקרוא can_execute פעמיים (כל קריאה צורכת slot ב-HALF_OPEN).
        cb_allows = await circuit_breaker.can_execute()

        # אם ה-CB פתוח (טלגרם למטה) — fast-fail, לא מנסים בכלל
        if not cb_allows:
            logger.info(
                "Circuit breaker open, skipping photo forward",
                extra_data={"chat_id": chat_id}
            )
            return False

        # ניסיון ראשון: sendPhoto — בלי circuit breaker כי כשלון כאן צפוי
        # (file_id ממסמך לא עובד עם sendPhoto).
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{base_url}/sendPhoto",
                    json={"chat_id": chat_id, "photo": file_id},
                    timeout=30.0,
                )
                if response.status_code == 200:
                    # דיווח הצלחה ל-CB כדי שלא יישאר תקוע ב-HALF_OPEN
                    await circuit_breaker.record_success()
                    return True
        except Exception:
            pass

        # fallback: sendDocument — ידנית (בלי cb.execute) כדי לא לצרוך slot נוסף
        logger.info(
            "sendPhoto failed, retrying with sendDocument",
            extra_data={"chat_id": chat_id}
        )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{base_url}/sendDocument",
                    json={"chat_id": chat_id, "document": file_id},
                    timeout=30.0,
                )
                if response.status_code != 200:
                    raise TelegramError.from_response(
                        "sendDocument",
                        response,
                        message=f"sendDocument returned status {response.status_code}",
                    )
                await circuit_breaker.record_success()
                return True
        except Exception as e:
            await circuit_breaker.record_failure(e)
            logger.error(
                "Error sending photo/document",
                extra_data={"chat_id": chat_id, "error": str(e)},
                exc_info=True,
            )
            return False

    @staticmethod
    async def _forward_whatsapp_photo_to_telegram(
        chat_id: str,
        file_id: str,
    ) -> bool:
        """הורדת מדיה ממקור WhatsApp והעלאתה לטלגרם כ-multipart upload.

        file_id יכול להיות:
        - URL של WPPConnect (http://...) — מוריד ישירות
        - Cloud API media ID — מוריד דרך Meta Graph API
        - data URI — מפענח את ה-base64
        """
        if not settings.TELEGRAM_BOT_TOKEN:
            return False

        # --- המרה ל-bytes ---
        image_bytes: bytes | None = None
        mime_type = "image/jpeg"

        if file_id.startswith("data:"):
            try:
                header, b64_data = file_id.split(",", 1)
                mime_type = header.split(":")[1].split(";")[0] if ":" in header else "image/jpeg"
                image_bytes = base64.b64decode(b64_data)
            except Exception:
                logger.warning("כשלון בפענוח data URI של צילום הפקדה")
                return False
        elif file_id.startswith(("http://", "https://")):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(file_id, timeout=30.0)
                    if resp.status_code == 200:
                        mime_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                        image_bytes = resp.content
            except Exception as e:
                logger.warning(
                    "כשלון בהורדת תמונת הפקדה מ-URL",
                    extra_data={"error": str(e)},
                )
                return False
        else:
            # Cloud API media ID — מוריד דרך Meta Graph API
            data_uri = await AdminNotificationService._download_cloud_api_media_as_data_url(file_id)
            if data_uri:
                try:
                    header, b64_data = data_uri.split(",", 1)
                    mime_type = header.split(":")[1].split(";")[0] if ":" in header else "image/jpeg"
                    image_bytes = base64.b64decode(b64_data)
                except Exception:
                    logger.warning("כשלון בפענוח data URI לאחר הורדה מ-Cloud API")
                    return False

        if not image_bytes:
            return False

        # --- העלאה לטלגרם ---
        ext = mimetypes.guess_extension(mime_type) or ".jpg"
        circuit_breaker = get_telegram_circuit_breaker()

        async def _upload() -> bool:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendPhoto",
                    data={"chat_id": chat_id},
                    files={"photo": (f"deposit{ext}", image_bytes, mime_type)},
                    timeout=30.0,
                )
                if response.status_code != 200:
                    raise TelegramError.from_response(
                        "sendPhoto (upload)",
                        response,
                        message=f"sendPhoto upload returned status {response.status_code}",
                    )
                return True

        try:
            return await circuit_breaker.execute(_upload)
        except Exception as e:
            logger.error(
                "כשלון בהעלאת צילום הפקדה לטלגרם",
                extra_data={"chat_id": chat_id, "error": str(e)},
                exc_info=True,
            )
            return False

    # ──────────────────────────────────────────────
    #  שיטות עזר - וואטסאפ
    # ──────────────────────────────────────────────

    @staticmethod
    def _get_admin_wa_provider(phone_or_group: str):
        """בחירת ספק WhatsApp להודעות מנהלים — ניתוב לפי סוג יעד.

        קבוצות (@g.us) → WPPConnect (Cloud API לא תומך בקבוצות לא רשמיות).
        מספרים פרטיים → admin provider (pywa במצב hybrid/pywa, WPPConnect אחרת).
        """
        if phone_or_group and "@g.us" in phone_or_group:
            return get_whatsapp_group_provider()
        return get_whatsapp_admin_provider()

    @staticmethod
    async def _send_whatsapp_admin_message(
        phone_or_group: str,
        text: str,
        keyboard: list = None
    ) -> bool:
        """שליחת הודעה למנהל/קבוצה בוואטסאפ — ניתוב לפי סוג יעד."""
        provider = AdminNotificationService._get_admin_wa_provider(phone_or_group)
        # WPPConnect דורש gateway URL; pywa לא (Cloud API ישיר)
        if provider.provider_name == "wppconnect" and not settings.WHATSAPP_GATEWAY_URL:
            logger.warning("WhatsApp gateway URL not configured for WPPConnect admin message")
            return False
        try:
            await provider.send_text(to=phone_or_group, text=text, keyboard=keyboard)
            return True
        except Exception as exc:
            logger.error(
                "כשלון בשליחת הודעת WhatsApp למנהל",
                extra_data={
                    "target": PhoneNumberValidator.mask(phone_or_group),
                    "error": str(exc),
                },
                exc_info=True,
            )
            return False

    @staticmethod
    async def _send_whatsapp_admin_photo(phone_or_group: str, media_url: str) -> bool:
        """שליחת תמונה למנהל/קבוצה בוואטסאפ — ניתוב לפי סוג יעד."""
        if not media_url:
            logger.warning("No media_url provided for WhatsApp admin photo")
            return False

        provider = AdminNotificationService._get_admin_wa_provider(phone_or_group)
        # WPPConnect דורש gateway URL; pywa לא (Cloud API ישיר)
        if provider.provider_name == "wppconnect" and not settings.WHATSAPP_GATEWAY_URL:
            logger.warning("WhatsApp gateway URL not configured for WPPConnect admin photo")
            return False
        try:
            await provider.send_media(
                to=phone_or_group, media_url=media_url, media_type="image"
            )
            return True
        except Exception as exc:
            logger.error(
                "כשלון בשליחת תמונה למנהל WhatsApp",
                extra_data={
                    "target": PhoneNumberValidator.mask(phone_or_group),
                    "error": str(exc),
                },
                exc_info=True,
            )
            return False
