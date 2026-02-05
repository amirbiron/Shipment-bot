"""
WhatsApp Webhook Handler - Bot Gateway Layer
"""
import asyncio
import re

import httpx
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User, UserRole, ApprovalStatus
from app.state_machine.handlers import SenderStateHandler, CourierStateHandler
from app.state_machine.states import CourierState
from app.state_machine.manager import StateManager
from app.domain.services import AdminNotificationService
from app.core.logging import get_logger
from app.core.circuit_breaker import get_whatsapp_circuit_breaker
from app.core.validation import PhoneNumberValidator, convert_html_to_whatsapp
from app.core.config import settings
from app.core.exceptions import WhatsAppError

logger = get_logger(__name__)

router = APIRouter()


class WhatsAppMessage(BaseModel):
    """Incoming WhatsApp message structure"""
    from_number: str
    # מזהה יציב לשיחה/שולח (למשל message.from של WPPConnect). אם לא נשלח, ניפול ל-from_number.
    sender_id: Optional[str] = None
    # יעד תשובה בפועל (יכול להיות phone@c.us או @lid). אם לא נשלח, ניפול ל-from_number.
    reply_to: Optional[str] = None
    message_id: str
    text: str = ""
    timestamp: int
    # Support for media messages
    media_url: Optional[str] = None
    media_type: Optional[str] = None


class WhatsAppWebhookPayload(BaseModel):
    """WhatsApp webhook payload"""
    messages: list[WhatsAppMessage] = []


async def get_or_create_user(
    db: AsyncSession,
    sender_identifier: str
) -> tuple[User, bool]:
    """
    Get existing user or create new one. Returns (user, is_new)

    בווטסאפ לא תמיד יש מספר טלפון יציב (למשל @lid), לכן אנחנו משתמשים במזהה שולח יציב
    בתור ה-"phone_number" במודל לצורך זיהוי ושמירת session.
    """
    result = await db.execute(
        select(User).where(User.phone_number == sender_identifier)
    )
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            phone_number=sender_identifier,
            platform="whatsapp",
            role=UserRole.SENDER
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user, True  # New user

    return user, False  # Existing user


async def send_whatsapp_message(phone_number: str, text: str, keyboard: list = None) -> None:
    """
    Send message via WhatsApp Gateway (Node.js microservice) with circuit breaker protection.
    ממיר אוטומטית תגי HTML לפורמט וואטסאפ.
    כולל retry עם exponential backoff לשגיאות זמניות (ניתן להגדרה ב-settings).
    """
    # המרת תגי HTML לפורמט וואטסאפ (לדוגמה: <b> -> *)
    formatted_text = convert_html_to_whatsapp(text)

    circuit_breaker = get_whatsapp_circuit_breaker()

    # הגדרות retry מה-config
    max_retries = settings.WHATSAPP_MAX_RETRIES
    transient_status_codes = {
        int(code.strip())
        for code in settings.WHATSAPP_TRANSIENT_STATUS_CODES.split(",")
        if code.strip()
    }

    async def _send_with_retry():
        # שימוש חוזר באותו client לכל הניסיונות - חוסך TCP+TLS handshake
        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(max_retries):
                try:
                    response = await client.post(
                        f"{settings.WHATSAPP_GATEWAY_URL}/send",
                        json={
                            "phone": phone_number,
                            "message": formatted_text,
                            "keyboard": keyboard
                        }
                    )
                    if response.status_code == 200:
                        return  # הצלחה

                    # בדיקה אם זו שגיאה זמנית שכדאי לנסות שוב
                    if response.status_code in transient_status_codes and attempt < max_retries - 1:
                        backoff_seconds = 2 ** attempt  # 1, 2, 4 שניות
                        logger.warning(
                            "WhatsApp send got transient error, retrying",
                            extra_data={
                                "phone": PhoneNumberValidator.mask(phone_number),
                                "status_code": response.status_code,
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "backoff_seconds": backoff_seconds
                            }
                        )
                        await asyncio.sleep(backoff_seconds)
                        continue

                    # שגיאה לא זמנית או מיצינו את הניסיונות
                    raise WhatsAppError.from_response(
                        "send",
                        response,
                        message=f"gateway /send returned status {response.status_code}",
                    )
                except httpx.TimeoutException:
                    # Timeout גם נחשב שגיאה זמנית
                    if attempt < max_retries - 1:
                        backoff_seconds = 2 ** attempt
                        logger.warning(
                            "WhatsApp send timeout, retrying",
                            extra_data={
                                "phone": PhoneNumberValidator.mask(phone_number),
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "backoff_seconds": backoff_seconds
                            }
                        )
                        await asyncio.sleep(backoff_seconds)
                        continue
                    raise WhatsAppError(
                        message="gateway /send timeout after retries",
                        details={"timeout": True, "attempts": max_retries}
                    )
                except httpx.RequestError as e:
                    # שגיאות רשת (connection error וכו')
                    if attempt < max_retries - 1:
                        backoff_seconds = 2 ** attempt
                        logger.warning(
                            "WhatsApp send network error, retrying",
                            extra_data={
                                "phone": PhoneNumberValidator.mask(phone_number),
                                "error": str(e),
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "backoff_seconds": backoff_seconds
                            }
                        )
                        await asyncio.sleep(backoff_seconds)
                        continue
                    raise WhatsAppError(
                        message=f"gateway /send network error: {str(e)}",
                        details={"network_error": True, "attempts": max_retries}
                    )

    try:
        await circuit_breaker.execute(_send_with_retry)
    except Exception as e:
        logger.error(
            "WhatsApp send failed",
            extra_data={"phone": PhoneNumberValidator.mask(phone_number), "error": str(e)},
            exc_info=True
        )


async def handle_admin_group_command(
    db: AsyncSession,
    text: str
) -> Optional[str]:
    """
    טיפול בפקודות מנהל מקבוצת הוואטסאפ.
    מזהה פקודות כמו "אשר שליח 123" או "דחה שליח 456"

    Returns:
        הודעת תגובה אם זוהתה פקודה, None אחרת
    """
    text = text.strip()

    # זיהוי פקודת אישור - תומך בפורמטים:
    # "אשר 123", "אשר שליח 123", "✅ אשר 123"
    # חייב להתחיל בתחילת ההודעה - מונע התאמה של ציטוטים
    approve_match = re.match(r'^[✅\s]*אשר(?:\s+שליח)?\s+(\d+)\s*$', text)
    if approve_match:
        user_id = int(approve_match.group(1))
        return await _approve_courier(db, user_id)

    # זיהוי פקודת דחייה - תומך בפורמטים:
    # "דחה 123", "דחה שליח 123", "❌ דחה 123"
    # חייב להתחיל בתחילת ההודעה - מונע התאמה של ציטוטים
    reject_match = re.match(r'^[❌\s]*דחה(?:\s+שליח)?\s+(\d+)\s*$', text)
    if reject_match:
        user_id = int(reject_match.group(1))
        return await _reject_courier(db, user_id)

    return None


async def _approve_courier(db: AsyncSession, user_id: int) -> str:
    """אישור שליח לפי user_id"""
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        return f"❌ לא נמצא משתמש עם מזהה {user_id}"

    if user.role != UserRole.COURIER:
        return f"❌ משתמש {user_id} אינו שליח"

    if user.approval_status == ApprovalStatus.APPROVED:
        return f"ℹ️ שליח {user_id} ({user.full_name or user.name}) כבר מאושר"

    # בדיקה אם השליח חסום - לא מאפשרים אישור של משתמש חסום
    if user.approval_status == ApprovalStatus.BLOCKED:
        return f"⛔ שליח {user_id} ({user.full_name or user.name}) חסום במערכת. לא ניתן לאשר משתמש חסום."

    # אישור השליח
    user.approval_status = ApprovalStatus.APPROVED
    await db.commit()

    logger.info(
        "Courier approved via WhatsApp admin group",
        extra_data={"user_id": user_id, "name": user.full_name or user.name}
    )

    # שליחת הודעה לשליח שהוא אושר
    if user.phone_number and not user.phone_number.endswith("@g.us"):
        # משתמש וואטסאפ
        approval_message = """🎉 *חשבונך אושר!*

ברוכים הבאים למערכת השליחים!
מעכשיו תוכל לתפוס משלוחים ולהתחיל לעבוד.

כתוב *תפריט* כדי להתחיל."""
        await send_whatsapp_message(user.phone_number, approval_message)
    elif user.telegram_chat_id:
        # משתמש טלגרם
        from app.api.webhooks.telegram import send_telegram_message
        approval_message = """🎉 <b>חשבונך אושר!</b>

ברוכים הבאים למערכת השליחים!
מעכשיו תוכל לתפוס משלוחים ולהתחיל לעבוד.

כתוב <b>תפריט</b> כדי להתחיל."""
        await send_telegram_message(user.telegram_chat_id, approval_message)

    return f"✅ שליח {user_id} ({user.full_name or user.name}) אושר בהצלחה!"


async def _reject_courier(db: AsyncSession, user_id: int) -> str:
    """דחיית שליח לפי user_id"""
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        return f"❌ לא נמצא משתמש עם מזהה {user_id}"

    if user.role != UserRole.COURIER:
        return f"❌ משתמש {user_id} אינו שליח"

    if user.approval_status == ApprovalStatus.REJECTED:
        return f"ℹ️ שליח {user_id} ({user.full_name or user.name}) כבר נדחה"

    # בדיקה אם השליח חסום - BLOCKED הוא סטטוס "דביק" שלא ניתן לשנות
    if user.approval_status == ApprovalStatus.BLOCKED:
        return f"⛔ שליח {user_id} ({user.full_name or user.name}) חסום במערכת. לא ניתן לשנות סטטוס של משתמש חסום."

    # דחיית השליח
    user.approval_status = ApprovalStatus.REJECTED
    await db.commit()

    logger.info(
        "Courier rejected via WhatsApp admin group",
        extra_data={"user_id": user_id, "name": user.full_name or user.name}
    )

    # שליחת הודעה לשליח שנדחה
    if user.phone_number and not user.phone_number.endswith("@g.us"):
        # משתמש וואטסאפ
        rejection_message = """😔 *לצערנו, בקשתך להצטרף כשליח נדחתה.*

אם אתה חושב שזו טעות, אנא צור קשר עם התמיכה."""
        await send_whatsapp_message(user.phone_number, rejection_message)
    elif user.telegram_chat_id:
        # משתמש טלגרם
        from app.api.webhooks.telegram import send_telegram_message
        rejection_message = """😔 <b>לצערנו, בקשתך להצטרף כשליח נדחתה.</b>

אם אתה חושב שזו טעות, אנא צור קשר עם התמיכה."""
        await send_telegram_message(user.telegram_chat_id, rejection_message)

    return f"❌ שליח {user_id} ({user.full_name or user.name}) נדחה."


async def send_welcome_message(phone_number: str):
    """הודעת ברוכים הבאים ותפריט ראשי [שלב 1]"""
    welcome_text = (
        "ברוכים הבאים ל*משלוח בצ'יק* 🚚\n"
        "המערכת החכמה לשיתוף משלוחים.\n\n"
        "איך נוכל לעזור היום?\n\n"
        "בכל שלב תוכלו לחזור לתפריט הראשי על ידי הקשה של #"
    )

    keyboard = [
        ["🚚 הצטרפות למנוי וקבלת משלוחים"],
        ["📦 העלאת משלוח מהיר"],
        ["🏪 הצטרפות כתחנה"],
        ["📞 פנייה לניהול"],
    ]
    await send_whatsapp_message(phone_number, welcome_text, keyboard)


@router.post(
    "/webhook",
    summary="Webhook - WhatsApp (קבלת הודעות נכנסות)",
    description=(
        "נקודת כניסה לקבלת הודעות מ-WhatsApp Gateway. "
        "מבצעת ניתוב לזרימת שולח/שליח לפי role ומנהלת state machine."
    ),
)
async def whatsapp_webhook(
    payload: WhatsAppWebhookPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle incoming WhatsApp messages.
    Routes to sender or courier handlers based on user role.
    """
    responses = []

    for message in payload.messages:
        text = message.text or ""
        sender_id = (message.sender_id or message.from_number or "").strip()
        reply_to = (message.reply_to or message.from_number or "").strip()
        # Accept image media (WPPConnect may return 'image' or have image in mimetype)
        photo_file_id = message.media_url if message.media_type and 'image' in message.media_type.lower() else None

        logger.debug(
            "WhatsApp message received",
            extra_data={
                "from": PhoneNumberValidator.mask(sender_id),
                "reply_to": PhoneNumberValidator.mask(reply_to),
                "text_preview": text[:50] if text else "",
                "media_type": message.media_type,
                "has_media_url": bool(message.media_url)
            }
        )

        # Skip empty messages
        if not text and not photo_file_id:
            continue

        # בדיקה אם ההודעה מגיעה מקבוצה (group ID מסתיים ב-@g.us)
        is_group_message = sender_id.endswith("@g.us")

        if is_group_message:
            # בדיקה אם זו קבוצת המנהלים
            if settings.WHATSAPP_ADMIN_GROUP_ID and sender_id == settings.WHATSAPP_ADMIN_GROUP_ID:
                logger.info(
                    "Admin group message received",
                    extra_data={"group_id": sender_id, "text": text[:50]}
                )

                # ניסיון לזהות פקודת מנהל
                response_text = await handle_admin_group_command(db, text)

                if response_text:
                    # שליחת תגובה לקבוצה
                    background_tasks.add_task(
                        send_whatsapp_message,
                        sender_id,  # שליחה לקבוצה
                        response_text
                    )
                    responses.append({
                        "from": sender_id,
                        "response": response_text,
                        "admin_command": True
                    })
                else:
                    # הודעה רגילה בקבוצה (לא פקודה) - מתעלמים
                    logger.debug("Non-command message in admin group, ignoring")

            else:
                # הודעה מקבוצה אחרת - מתעלמים
                logger.debug(
                    "Message from non-admin group, ignoring",
                    extra_data={"group_id": sender_id}
                )

            continue  # לא ממשיכים לטיפול רגיל בהודעות מקבוצות

        # Get or create user
        user, is_new_user = await get_or_create_user(db, sender_id)

        # Initialize state manager
        state_manager = StateManager(db)

        # New user - show welcome message with role selection [1.1]
        if is_new_user:
            background_tasks.add_task(send_welcome_message, reply_to)
            responses.append({
                "from": sender_id,
                "response": "welcome",
                "new_user": True
            })
            continue

        # Handle "#" to return to main menu
        if text.strip() == "#":
            # רענון מהDB לפני בדיקת סטטוס - למניעת stale data אם האדמין אישר בינתיים
            await db.refresh(user)
            # לוג לדיבאג - מראה את מצב המשתמש בלחיצה על #
            logger.info(
                "User pressed # to return to menu",
                extra_data={
                    "user_id": user.id,
                    "phone": PhoneNumberValidator.mask(sender_id),
                    "role": user.role.value if user.role else None,
                    "approval_status": user.approval_status.value if user.approval_status else None
                }
            )
            # Reset state to menu
            if user.role == UserRole.COURIER:
                # בדיקה אם השליח לא מאושר (כולל None, PENDING, REJECTED, BLOCKED)
                # אפשר לו לחזור להיות שולח רגיל
                if user.approval_status != ApprovalStatus.APPROVED:
                    # מחזירים אותו להיות שולח רגיל
                    logger.info(
                        "Non-approved courier pressed #, switching to sender",
                        extra_data={
                            "user_id": user.id,
                            "phone": PhoneNumberValidator.mask(sender_id),
                            "reply_to": PhoneNumberValidator.mask(reply_to)
                        }
                    )
                    user.role = UserRole.SENDER
                    await db.commit()
                    # מאפסים את ה-state machine ומנקים context
                    from app.state_machine.states import SenderState
                    await state_manager.force_state(user.id, "whatsapp", SenderState.MENU.value, context={})
                    # מציגים הודעת ברוכים הבאים מחדש
                    background_tasks.add_task(send_welcome_message, reply_to)
                    responses.append({
                        "from": sender_id,
                        "response": "welcome (switched from non-approved courier)",
                        "new_state": SenderState.MENU.value
                    })
                    continue

                await state_manager.force_state(user.id, "whatsapp", CourierState.MENU.value, context={})
                handler = CourierStateHandler(db, platform="whatsapp")
                response, new_state = await handler.handle_message(user, "תפריט", None)
            else:
                from app.state_machine.states import SenderState
                await state_manager.force_state(user.id, "whatsapp", SenderState.MENU.value, context={})
                handler = SenderStateHandler(db)
                response, new_state = await handler.handle_message(
                    user_id=user.id,
                    platform="whatsapp",
                    message="תפריט"
                )

            background_tasks.add_task(
                send_whatsapp_message,
                reply_to,
                response.text,
                response.keyboard
            )
            responses.append({
                "from": sender_id,
                "response": response.text,
                "new_state": new_state
            })
            continue

        # טיפול בכפתורי תפריט ראשי [שלב 1]
        # הערה: הכפתורים הבאים פעילים רק למשתמשים שאינם שליחים באמצע תהליך רישום.
        # שליח באמצע KYC ימשיך ישירות ל-CourierStateHandler למטה.
        _is_courier_in_registration = (
            user.role == UserRole.COURIER
            and await state_manager.get_current_state(user.id, "whatsapp") in {
                CourierState.REGISTER_COLLECT_NAME.value,
                CourierState.REGISTER_COLLECT_DOCUMENT.value,
                CourierState.REGISTER_COLLECT_SELFIE.value,
                CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value,
                CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value,
                CourierState.REGISTER_TERMS.value,
            }
        )

        if not _is_courier_in_registration:
            if user.role == UserRole.SENDER and ("הצטרפות למנוי" in text or "שליח" in text):
                # ניתוב לתהליך הרישום כנהג/שליח
                user.role = UserRole.COURIER
                await db.commit()

                await state_manager.force_state(
                    user.id, "whatsapp",
                    CourierState.INITIAL.value,
                    context={}
                )

                handler = CourierStateHandler(db, platform="whatsapp")
                response, new_state = await handler.handle_message(user, text, photo_file_id)

                background_tasks.add_task(
                    send_whatsapp_message,
                    reply_to,
                    response.text,
                    response.keyboard
                )
                responses.append({
                    "from": sender_id,
                    "response": response.text,
                    "new_state": new_state
                })
                continue

            if "העלאת משלוח מהיר" in text or "משלוח מהיר" in text:
                # קישור חיצוני לקבוצת WhatsApp - משתמשים רגילים לא יכולים להעלות משלוח בתוך הבוט
                if settings.WHATSAPP_GROUP_LINK:
                    msg_text = (
                        "📦 *העלאת משלוח מהיר*\n\n"
                        "להעלאת משלוח מהיר, הצטרפו לקבוצת WhatsApp שלנו:\n"
                        f"{settings.WHATSAPP_GROUP_LINK}"
                    )
                else:
                    msg_text = (
                        "📦 *העלאת משלוח מהיר*\n\n"
                        "להעלאת משלוח מהיר, פנו להנהלה לקבלת קישור לקבוצת WhatsApp."
                    )
                background_tasks.add_task(send_whatsapp_message, reply_to, msg_text)
                responses.append({"from": sender_id, "response": msg_text, "new_state": None})
                continue

            if "הצטרפות כתחנה" in text or "תחנה" in text:
                # הודעה שיווקית עבור תחנות
                station_text = (
                    "🏪 *הצטרפות כתחנה*\n\n"
                    "המערכת של ShipShare מסדרת לך את התחנה!\n\n"
                    "✅ ניהול נהגים אוטומטי\n"
                    "✅ גבייה מסודרת\n"
                    "✅ תיעוד משלוחים מלא\n"
                    "✅ סדר בבלגן\n\n"
                    "לפרטים נוספים, פנו להנהלה."
                )
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, station_text,
                    [["📞 פנייה לניהול"]]
                )
                responses.append({"from": sender_id, "response": station_text, "new_state": None})
                continue

            if "פנייה לניהול" in text:
                # קישור WhatsApp ישיר למנהל הראשי
                if settings.ADMIN_WHATSAPP_NUMBER:
                    admin_link = f"https://wa.me/{settings.ADMIN_WHATSAPP_NUMBER}"
                    admin_text = (
                        "📞 *פנייה לניהול*\n\n"
                        f"ליצירת קשר עם המנהל:\n{admin_link}"
                    )
                else:
                    admin_text = (
                        "📞 *פנייה לניהול*\n\n"
                        "ליצירת קשר עם המנהל, שלחו הודעה כאן ונחזור אליכם בהקדם."
                    )
                background_tasks.add_task(send_whatsapp_message, reply_to, admin_text)
                responses.append({"from": sender_id, "response": admin_text, "new_state": None})
                continue

            if "חזרה לתפריט" in text:
                # כפתור "חזרה לתפריט" - מנתב כמו לחיצה על #
                background_tasks.add_task(send_welcome_message, reply_to)
                responses.append({"from": sender_id, "response": "welcome", "new_state": None})
                continue

        # Route based on user role
        if user.role == UserRole.COURIER:
            # שמירת המצב הקודם לפני הטיפול בהודעה
            previous_state = await state_manager.get_current_state(user.id, "whatsapp")

            handler = CourierStateHandler(db, platform="whatsapp")
            response, new_state = await handler.handle_message(user, text, photo_file_id)

            # שליחת "כרטיס נהג" למנהלים רק במעבר הראשון למצב PENDING_APPROVAL
            # (כלומר רק כשהמצב הקודם היה שונה - למניעת שליחה כפולה)
            if (new_state == CourierState.PENDING_APPROVAL.value and
                previous_state != CourierState.PENDING_APPROVAL.value and
                user.approval_status == ApprovalStatus.PENDING):
                context = await state_manager.get_context(user.id, "whatsapp")
                background_tasks.add_task(
                    AdminNotificationService.notify_new_courier_registration,
                    user.id,
                    user.full_name or user.name or "לא צוין",
                    user.service_area or "לא צוין",
                    user.phone_number,
                    context.get("document_file_id"),
                    "whatsapp",
                    user.vehicle_category,
                    user.selfie_file_id,
                    user.vehicle_photo_file_id,
                )

            # Check if courier submitted deposit screenshot
            if photo_file_id:
                context = await state_manager.get_context(user.id, "whatsapp")
                if context.get("deposit_screenshot"):
                    background_tasks.add_task(
                        AdminNotificationService.notify_deposit_request,
                        user.id,
                        user.full_name or user.name or "לא ידוע",
                        user.phone_number,
                        photo_file_id
                    )

            background_tasks.add_task(
                send_whatsapp_message,
                reply_to,
                response.text,
                response.keyboard
            )
            responses.append({
                "from": sender_id,
                "response": response.text,
                "new_state": new_state
            })
            continue

        # Sender flow - check if starting new delivery
        if "שלוח" in text or "חבילה" in text:
            handler = SenderStateHandler(db)
            response, new_state = await handler.handle_message(
                user_id=user.id,
                platform="whatsapp",
                message=text
            )

            background_tasks.add_task(
                send_whatsapp_message,
                reply_to,
                response.text,
                response.keyboard
            )
            responses.append({
                "from": sender_id,
                "response": response.text,
                "new_state": new_state
            })
            continue

        # Check current state for senders
        current_state = await state_manager.get_current_state(user.id, "whatsapp")

        # If user is in the middle of a sender flow, continue it
        if current_state and not current_state.startswith("COURIER.") and current_state not in ["INITIAL", "SENDER.INITIAL"]:
            handler = SenderStateHandler(db)
            response, new_state = await handler.handle_message(
                user_id=user.id,
                platform="whatsapp",
                message=text
            )

            background_tasks.add_task(
                send_whatsapp_message,
                reply_to,
                response.text,
                response.keyboard
            )
            responses.append({
                "from": sender_id,
                "response": response.text,
                "new_state": new_state
            })
            continue

        # Default: show welcome message with role selection
        background_tasks.add_task(send_welcome_message, reply_to)
        responses.append({
            "from": sender_id,
            "response": "welcome",
            "new_state": None
        })

    return {"processed": len(responses), "responses": responses}


@router.get(
    "/webhook",
    summary="Webhook Verification - WhatsApp",
    description="אימות webhook (challenge) עבור WhatsApp Business API.",
)
async def whatsapp_verify(
    hub_mode: str = None,
    hub_challenge: str = None,
    hub_verify_token: str = None
):
    """Webhook verification for WhatsApp Business API"""
    if hub_mode == "subscribe" and hub_challenge:
        return int(hub_challenge)
    return {"status": "ok"}
