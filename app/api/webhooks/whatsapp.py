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
from app.state_machine.states import CourierState, DispatcherState, StationOwnerState
from app.state_machine.dispatcher_handler import DispatcherStateHandler
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager
from app.domain.services import AdminNotificationService
from app.domain.services.courier_approval_service import CourierApprovalService
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
    # סוג MIME של המדיה (למשל image/jpeg) - לזיהוי מסמכים שהם בעצם תמונות
    mime_type: Optional[str] = None


class WhatsAppWebhookPayload(BaseModel):
    """WhatsApp webhook payload"""

    messages: list[WhatsAppMessage] = []


async def get_or_create_user(
    db: AsyncSession, sender_identifier: str
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
            phone_number=sender_identifier, platform="whatsapp", role=UserRole.SENDER
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user, True  # New user

    return user, False  # Existing user


async def send_whatsapp_message(
    phone_number: str, text: str, keyboard: list = None
) -> None:
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
                            "keyboard": keyboard,
                        },
                    )
                    if response.status_code == 200:
                        return  # הצלחה

                    # בדיקה אם זו שגיאה זמנית שכדאי לנסות שוב
                    if (
                        response.status_code in transient_status_codes
                        and attempt < max_retries - 1
                    ):
                        backoff_seconds = 2**attempt  # 1, 2, 4 שניות
                        logger.warning(
                            "WhatsApp send got transient error, retrying",
                            extra_data={
                                "phone": PhoneNumberValidator.mask(phone_number),
                                "status_code": response.status_code,
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "backoff_seconds": backoff_seconds,
                            },
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
                        backoff_seconds = 2**attempt
                        logger.warning(
                            "WhatsApp send timeout, retrying",
                            extra_data={
                                "phone": PhoneNumberValidator.mask(phone_number),
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "backoff_seconds": backoff_seconds,
                            },
                        )
                        await asyncio.sleep(backoff_seconds)
                        continue
                    raise WhatsAppError(
                        message="gateway /send timeout after retries",
                        details={"timeout": True, "attempts": max_retries},
                    )
                except httpx.RequestError as e:
                    # שגיאות רשת (connection error וכו')
                    if attempt < max_retries - 1:
                        backoff_seconds = 2**attempt
                        logger.warning(
                            "WhatsApp send network error, retrying",
                            extra_data={
                                "phone": PhoneNumberValidator.mask(phone_number),
                                "error": str(e),
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "backoff_seconds": backoff_seconds,
                            },
                        )
                        await asyncio.sleep(backoff_seconds)
                        continue
                    raise WhatsAppError(
                        message=f"gateway /send network error: {str(e)}",
                        details={"network_error": True, "attempts": max_retries},
                    )

    try:
        await circuit_breaker.execute(_send_with_retry)
    except Exception as e:
        logger.error(
            "WhatsApp send failed",
            extra_data={
                "phone": PhoneNumberValidator.mask(phone_number),
                "error": str(e),
            },
            exc_info=True,
        )


def _normalize_whatsapp_identifier(value: str) -> str:
    """נרמול מזהה וואטסאפ (מספר/מזהה) להשוואה עקבית"""
    if not value:
        return ""
    base = value.strip()
    if "@" in base:
        base = base.split("@")[0]
    digits = re.sub(r"\D", "", base)
    if not digits:
        return ""
    if digits.startswith("0"):
        digits = "972" + digits[1:]
    return digits


def _get_whatsapp_admin_numbers() -> set[str]:
    """מחזיר סט מספרי מנהלים פרטיים לוואטסאפ (מנורמלים)"""
    normalized = set()
    for raw in settings.WHATSAPP_ADMIN_NUMBERS.split(","):
        raw = raw.strip()
        if not raw:
            continue
        normalized_value = _normalize_whatsapp_identifier(raw)
        if normalized_value:
            normalized.add(normalized_value)
    return normalized


def _is_whatsapp_admin(sender_id: str) -> bool:
    """
    בדיקה אם השולח הוא מנהל - תומך בנרמול:
    - @lid / @c.us
    - 050... לעומת 972...
    - +972 מול 972
    """
    wa_admin_numbers = _get_whatsapp_admin_numbers()
    if not wa_admin_numbers:
        return False
    normalized_sender = _normalize_whatsapp_identifier(sender_id)
    if not normalized_sender:
        return False
    return normalized_sender in wa_admin_numbers


def _resolve_admin_send_target(sender_id: str, reply_to: str) -> str:
    """
    מציאת כתובת שליחה למנהל — מעדיף את המספר מההגדרות (שאנחנו יודעים שעובד).

    כרטיס הנהג נשלח למנהל דרך המספר שבהגדרות (WHATSAPP_ADMIN_NUMBERS) ומגיע בהצלחה.
    אבל כש-reply_to הוא @lid, הגטוויי עשוי לא להצליח לשלוח אליו.
    לכן אם אנחנו מזהים שה-sender_id תואם למספר מנהל מההגדרות — נשלח למספר ההגדרות.
    """
    normalized_sender = _normalize_whatsapp_identifier(sender_id)
    if not normalized_sender:
        return reply_to

    for raw in settings.WHATSAPP_ADMIN_NUMBERS.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if _normalize_whatsapp_identifier(raw) == normalized_sender:
            logger.debug(
                "Using admin number from settings for reply",
                extra_data={
                    "original_reply_to": PhoneNumberValidator.mask(reply_to),
                    "resolved_to": PhoneNumberValidator.mask(raw),
                }
            )
            return raw

    return reply_to


def _match_approval_command(text: str) -> tuple[str, int] | None:
    """
    זיהוי פקודת אישור/דחייה בטקסט.
    מחזיר (action, user_id) או None.
    תומך באמוג'י שונים (✅✔️☑️), רווחים מרובים, וניקוד (כוכביות מ-WhatsApp).
    """
    # ניקוי: הסרת כוכביות (bold של WhatsApp), תווים בלתי-נראים (zero-width, RTL/LTR marks),
    # ורווחים עודפים — WhatsApp עשוי להזריק תווי Unicode בלתי-נראים
    text = text.strip().replace("*", "")
    text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\ufeff]', '', text)
    text = re.sub(r'\s+', ' ', text)

    approve_match = re.match(r'^[✅✔️☑️\s]*(?:אשר|אישור)(?:\s+(?:שליח|נהג))?\s+(\d+)\s*$', text)
    if approve_match:
        return ("approve", int(approve_match.group(1)))

    reject_match = re.match(r'^[❌✖️\s]*(?:דחה|דחייה|דחיה)(?:\s+(?:שליח|נהג))?\s+(\d+)\s*$', text)
    if reject_match:
        return ("reject", int(reject_match.group(1)))

    return None


async def _handle_whatsapp_approval(
    db: AsyncSession,
    action: str,
    courier_id: int,
    admin_name: str,
    background_tasks: BackgroundTasks = None,
) -> str:
    """
    ביצוע אישור/דחייה + שליחת הודעה לשליח + סיכום לקבוצה.
    משותף לפקודות מקבוצה ומפרטי.
    """
    if action == "approve":
        result = await CourierApprovalService.approve(db, courier_id)
    else:
        result = await CourierApprovalService.reject(db, courier_id)

    if not result.success:
        return result.message

    # הודעה לשליח וסיכום לקבוצה - ברקע כדי לא לחסום את ה-webhook
    from app.api.webhooks.telegram import send_telegram_message

    if background_tasks:
        background_tasks.add_task(
            CourierApprovalService.notify_after_decision,
            result.user,
            action,
            admin_name,
            send_telegram_fn=send_telegram_message,
            send_whatsapp_fn=send_whatsapp_message,
        )
    else:
        await CourierApprovalService.notify_after_decision(
            result.user,
            action,
            admin_name,
            send_telegram_fn=send_telegram_message,
            send_whatsapp_fn=send_whatsapp_message,
        )

    return result.message


async def handle_admin_group_command(
    db: AsyncSession,
    text: str,
    background_tasks: BackgroundTasks = None,
) -> Optional[str]:
    """
    טיפול בפקודות מנהל מקבוצת הוואטסאפ (תאימות לאחור).
    מזהה פקודות כמו "אשר שליח 123" או "דחה שליח 456"
    """
    parsed = _match_approval_command(text)
    if not parsed:
        return None

    action, user_id = parsed
    return await _handle_whatsapp_approval(
        db,
        action,
        user_id,
        admin_name="מנהל (קבוצה)",
        background_tasks=background_tasks,
    )


async def handle_admin_private_command(
    db: AsyncSession,
    text: str,
    admin_name: str,
    background_tasks: BackgroundTasks = None,
) -> Optional[str]:
    """
    טיפול בפקודות אישור/דחייה מהודעות פרטיות של מנהלים.
    """
    parsed = _match_approval_command(text)
    if not parsed:
        return None

    action, user_id = parsed
    return await _handle_whatsapp_approval(
        db,
        action,
        user_id,
        admin_name=admin_name,
        background_tasks=background_tasks,
    )


async def _sender_fallback_wa(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
) -> tuple:
    """fallback לתפריט שולח — גרסת WhatsApp"""
    from app.state_machine.states import SenderState

    await state_manager.force_state(
        user.id, "whatsapp", SenderState.MENU.value, context={}
    )
    handler = SenderStateHandler(db)
    return await handler.handle_message(
        user_id=user.id, platform="whatsapp", message="תפריט"
    )


async def _route_to_role_menu_wa(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
) -> tuple:
    """
    ניתוב לתפריט הנכון לפי תפקיד — גרסת WhatsApp.

    חובה: כל תפקיד (UserRole) חייב להיות מטופל כאן במפורש.
    """
    if user.role == UserRole.COURIER:
        await state_manager.force_state(
            user.id, "whatsapp", CourierState.MENU.value, context={}
        )
        handler = CourierStateHandler(db, platform="whatsapp")
        return await handler.handle_message(user, "תפריט", None)

    if user.role == UserRole.STATION_OWNER:
        from app.domain.services.station_service import StationService

        station_service = StationService(db)
        station = await station_service.get_station_by_owner(user.id)

        if station:
            await state_manager.force_state(
                user.id, "whatsapp", StationOwnerState.MENU.value, context={}
            )
            handler = StationOwnerStateHandler(db, station.id, platform="whatsapp")
            return await handler.handle_message(user, "תפריט", None)
        # בעל תחנה ללא תחנה פעילה - הורדת תפקיד לשולח
        logger.warning(
            "Station owner without active station, downgrading to sender",
            extra_data={"user_id": user.id},
        )
        user.role = UserRole.SENDER
        await db.commit()
        return await _sender_fallback_wa(user, db, state_manager)

    if user.role == UserRole.SENDER or user.role == UserRole.ADMIN:
        return await _sender_fallback_wa(user, db, state_manager)

    # תפקיד לא מוכר
    logger.warning(
        "Unknown user role in menu routing, falling back to sender",
        extra_data={"user_id": user.id, "role": str(user.role)},
    )
    return await _sender_fallback_wa(user, db, state_manager)


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
    db: AsyncSession = Depends(get_db),
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
        # תמונות רגילות (media_type מכיל 'image')
        # או מסמך שהוא בעצם תמונה (media_type=document + mime_type מתחיל ב-image/)
        if message.media_url and message.media_type:
            mt = message.media_type.lower()
            if "image" in mt:
                photo_file_id = message.media_url
            elif 'document' in mt and message.mime_type and message.mime_type.lower().startswith('image/'):
                photo_file_id = message.media_url
            else:
                photo_file_id = None
        else:
            photo_file_id = None

        logger.debug(
            "WhatsApp message received",
            extra_data={
                "from": PhoneNumberValidator.mask(sender_id),
                "reply_to": PhoneNumberValidator.mask(reply_to),
                "text_preview": text[:50] if text else "",
                "media_type": message.media_type,
                "has_media_url": bool(message.media_url),
            },
        )

        # Skip empty messages
        if not text and not photo_file_id:
            continue

        # בדיקה אם ההודעה מגיעה מקבוצה (group ID מסתיים ב-@g.us)
        is_group_message = sender_id.endswith("@g.us")

        if is_group_message:
            # בדיקה אם זו קבוצת המנהלים
            if (
                settings.WHATSAPP_ADMIN_GROUP_ID
                and sender_id == settings.WHATSAPP_ADMIN_GROUP_ID
            ):
                logger.info(
                    "Admin group message received",
                    extra_data={"group_id": sender_id, "text": text[:50]},
                )

                # ניסיון לזהות פקודת מנהל
                response_text = await handle_admin_group_command(
                    db, text, background_tasks=background_tasks
                )

                if response_text:
                    # שליחת תגובה לקבוצה
                    background_tasks.add_task(
                        send_whatsapp_message, sender_id, response_text  # שליחה לקבוצה
                    )
                    responses.append(
                        {
                            "from": sender_id,
                            "response": response_text,
                            "admin_command": True,
                        }
                    )
                else:
                    # הודעה רגילה בקבוצה (לא פקודה) - מתעלמים
                    logger.debug("Non-command message in admin group, ignoring")

            else:
                # הודעה מקבוצה אחרת - מתעלמים
                logger.debug(
                    "Message from non-admin group, ignoring",
                    extra_data={"group_id": sender_id},
                )

            continue  # לא ממשיכים לטיפול רגיל בהודעות מקבוצות

        # Get or create user
        user, is_new_user = await get_or_create_user(db, sender_id)

        # טיפול בפקודות אישור/דחייה מהודעות פרטיות של מנהלים
        # חייב להיות לפני בדיקת is_new_user כדי שמנהל חדש שעוד לא ב-DB
        # יוכל לאשר/לדחות שליחים כבר מההודעה הראשונה שלו
        if _is_whatsapp_admin(sender_id) and text:
            admin_response = await handle_admin_private_command(
                db,
                text,
                admin_name=user.name or PhoneNumberValidator.mask(sender_id),
                background_tasks=background_tasks,
            )
            if admin_response:
                # שליחת התגובה למספר המנהל מההגדרות (שאנחנו יודעים שעובד)
                # במקום ל-reply_to (שעלול להיות @lid שהגטוויי לא יודע לשלוח אליו)
                admin_send_to = _resolve_admin_send_target(sender_id, reply_to)
                background_tasks.add_task(send_whatsapp_message, admin_send_to, admin_response)
                responses.append({
                    "from": sender_id,
                    "response": admin_response,
                    "admin_command": True
                })
                continue

        # Initialize state manager
        state_manager = StateManager(db)

        # New user - show welcome message with role selection [1.1]
        if is_new_user:
            background_tasks.add_task(send_welcome_message, reply_to)
            responses.append(
                {"from": sender_id, "response": "welcome", "new_user": True}
            )
            continue

        # Handle "#" to return to main menu
        if text.strip() in {"#", "תפריט ראשי"}:
            # רענון מהDB לפני בדיקת סטטוס - למניעת stale data אם האדמין אישר בינתיים
            await db.refresh(user)
            # לוג לדיבאג - מראה את מצב המשתמש בלחיצה על #
            logger.info(
                "User pressed # to return to menu",
                extra_data={
                    "user_id": user.id,
                    "phone": PhoneNumberValidator.mask(sender_id),
                    "role": user.role.value if user.role else None,
                    "approval_status": (
                        user.approval_status.value if user.approval_status else None
                    ),
                },
            )

            # אדמין (לפי WHATSAPP_ADMIN_NUMBERS): מאפשרים יציאה "קשיחה" מכל זרימה וחזרה לתפריט הראשי
            # של כל אפשרויות הרישום - בלי לשנות role ב-DB (כדי לא למחוק STATION_OWNER וכו').
            if _is_whatsapp_admin(sender_id):
                from app.state_machine.states import SenderState

                # איפוס state כדי לאפשר עבודה עם תפריט ראשי גם אם האדמין היה באמצע זרימה רב-שלבית כשליח
                await state_manager.force_state(
                    user.id,
                    "whatsapp",
                    SenderState.MENU.value,
                    context={"admin_root_menu": True},
                )

                background_tasks.add_task(send_welcome_message, reply_to)
                responses.append(
                    {
                        "from": sender_id,
                        "response": "welcome (admin main menu)",
                        "new_state": SenderState.MENU.value,
                        "admin_main_menu": True,
                    }
                )
                continue

            # Reset state to menu
            if (
                user.role == UserRole.COURIER
                and user.approval_status != ApprovalStatus.APPROVED
            ):
                # שליח לא מאושר - מחזירים אותו להיות שולח רגיל
                logger.info(
                    "Non-approved courier pressed #, switching to sender",
                    extra_data={
                        "user_id": user.id,
                        "phone": PhoneNumberValidator.mask(sender_id),
                        "reply_to": PhoneNumberValidator.mask(reply_to),
                    },
                )
                user.role = UserRole.SENDER
                await db.commit()
                from app.state_machine.states import SenderState

                await state_manager.force_state(
                    user.id, "whatsapp", SenderState.MENU.value, context={}
                )
                background_tasks.add_task(send_welcome_message, reply_to)
                responses.append(
                    {
                        "from": sender_id,
                        "response": "welcome (switched from non-approved courier)",
                        "new_state": SenderState.MENU.value,
                    }
                )
                continue

            response, new_state = await _route_to_role_menu_wa(user, db, state_manager)

            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard
            )
            responses.append(
                {"from": sender_id, "response": response.text, "new_state": new_state}
            )
            continue

        # טיפול בכפתורי תפריט ראשי [שלב 1]
        # הכפתורים פעילים רק למשתמשים שאינם באמצע זרימה רב-שלבית
        # (רישום שליח, זרימת סדרן, זרימת בעל תחנה)
        _current_state_value = await state_manager.get_current_state(
            user.id, "whatsapp"
        )
        _is_courier_in_registration = (
            user.role == UserRole.COURIER
            and _current_state_value
            in {
                CourierState.REGISTER_COLLECT_NAME.value,
                CourierState.REGISTER_COLLECT_DOCUMENT.value,
                CourierState.REGISTER_COLLECT_SELFIE.value,
                CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value,
                CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value,
                CourierState.REGISTER_TERMS.value,
            }
        )
        _is_in_multi_step_flow = _is_courier_in_registration or (
            isinstance(_current_state_value, str)
            and _current_state_value.startswith(("DISPATCHER.", "STATION."))
        )
        _context = await state_manager.get_context(user.id, "whatsapp")
        _admin_root_menu = bool(_context.get("admin_root_menu")) and _is_whatsapp_admin(
            sender_id
        )

        if not _is_in_multi_step_flow:
            if (
                user.role in (UserRole.SENDER, UserRole.ADMIN) or _admin_root_menu
            ) and ("הצטרפות למנוי" in text or "שליח" in text):
                # ניתוב לתהליך הרישום כנהג/שליח
                user.role = UserRole.COURIER
                await db.commit()

                await state_manager.force_state(
                    user.id, "whatsapp", CourierState.INITIAL.value, context={}
                )

                handler = CourierStateHandler(db, platform="whatsapp")
                response, new_state = await handler.handle_message(
                    user, text, photo_file_id
                )

                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {
                        "from": sender_id,
                        "response": response.text,
                        "new_state": new_state,
                    }
                )
                continue

            if ("העלאת משלוח מהיר" in text or "משלוח מהיר" in text) and (
                user.role in (UserRole.SENDER, UserRole.ADMIN) or _admin_root_menu
            ):
                # קישור חיצוני לקבוצת WhatsApp
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
                responses.append(
                    {"from": sender_id, "response": msg_text, "new_state": None}
                )
                continue

            if ("הצטרפות כתחנה" in text or "תחנה" in text) and (
                user.role in (UserRole.SENDER, UserRole.ADMIN) or _admin_root_menu
            ):
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
                    send_whatsapp_message, reply_to, station_text, [["📞 פנייה לניהול"]]
                )
                responses.append(
                    {"from": sender_id, "response": station_text, "new_state": None}
                )
                continue

            if "פנייה לניהול" in text and (
                user.role in (UserRole.SENDER, UserRole.ADMIN) or _admin_root_menu
            ):
                # קישור WhatsApp ישיר למנהל הראשי
                if settings.ADMIN_WHATSAPP_NUMBER:
                    admin_link = f"https://wa.me/{settings.ADMIN_WHATSAPP_NUMBER}"
                    admin_text = (
                        "📞 *פנייה לניהול*\n\n" f"ליצירת קשר עם המנהל:\n{admin_link}"
                    )
                else:
                    admin_text = (
                        "📞 *פנייה לניהול*\n\n"
                        "ליצירת קשר עם המנהל, שלחו הודעה כאן ונחזור אליכם בהקדם."
                    )
                background_tasks.add_task(send_whatsapp_message, reply_to, admin_text)
                responses.append(
                    {"from": sender_id, "response": admin_text, "new_state": None}
                )
                continue

            if "חזרה לתפריט" in text and (
                user.role not in (UserRole.COURIER, UserRole.STATION_OWNER)
                or _admin_root_menu
            ):
                # כפתור "חזרה לתפריט" - שולחים רגילים חוזרים לתפריט הראשי
                background_tasks.add_task(send_welcome_message, reply_to)
                responses.append(
                    {"from": sender_id, "response": "welcome", "new_state": None}
                )
                continue

        # ==================== ניתוב לפי תפקיד [שלב 3] ====================

        current_state = _current_state_value

        # ניתוב לבעל תחנה [שלב 3.3]
        if user.role == UserRole.STATION_OWNER:
            from app.domain.services.station_service import StationService

            station_service = StationService(db)
            station = await station_service.get_station_by_owner(user.id)

            if station:
                handler = StationOwnerStateHandler(db, station.id, platform="whatsapp")
                response, new_state = await handler.handle_message(
                    user, text, photo_file_id
                )
            else:
                # בעל תחנה ללא תחנה פעילה - fallback
                response, new_state = await _route_to_role_menu_wa(
                    user, db, state_manager
                )

            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard
            )
            responses.append(
                {"from": sender_id, "response": response.text, "new_state": new_state}
            )
            continue

        # ניתוב לתפריט סדרן (כפתור "תפריט סדרן" בתפריט נהג) [שלב 3.2]
        if (
            "תפריט סדרן" in text or "🏪 תפריט סדרן" in text
        ) and user.role == UserRole.COURIER:
            from app.domain.services.station_service import StationService

            station_service = StationService(db)
            station = await station_service.get_dispatcher_station(user.id)

            if station:
                await state_manager.force_state(
                    user.id, "whatsapp", DispatcherState.MENU.value, context={}
                )
                handler = DispatcherStateHandler(db, station.id, platform="whatsapp")
                response, new_state = await handler.handle_message(user, "תפריט", None)
            else:
                # סדרן הוסר או תחנה בוטלה
                logger.warning(
                    "Dispatcher clicked station menu but station not found",
                    extra_data={"user_id": user.id},
                )
                response, new_state = await _route_to_role_menu_wa(
                    user, db, state_manager
                )

            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard
            )
            responses.append(
                {"from": sender_id, "response": response.text, "new_state": new_state}
            )
            continue

        # אם המשתמש באמצע זרימת סדרן - ממשיכים עם DispatcherStateHandler
        if current_state and current_state.startswith("DISPATCHER."):
            from app.domain.services.station_service import StationService

            station_service = StationService(db)
            station = await station_service.get_dispatcher_station(user.id)

            if station:
                # כפתור "חזרה לתפריט נהג" מחזיר לתפריט הנהג הרגיל
                if "חזרה לתפריט נהג" in text:
                    await state_manager.force_state(
                        user.id, "whatsapp", CourierState.MENU.value, context={}
                    )
                    handler = CourierStateHandler(db, platform="whatsapp")
                    response, new_state = await handler.handle_message(
                        user, "תפריט", None
                    )
                else:
                    handler = DispatcherStateHandler(
                        db, station.id, platform="whatsapp"
                    )
                    response, new_state = await handler.handle_message(
                        user, text, photo_file_id
                    )
            else:
                # תחנה לא נמצאה - איפוס לתפריט נהג
                logger.warning(
                    "Dispatcher station not found, resetting to courier menu",
                    extra_data={"user_id": user.id, "state": current_state},
                )
                response, new_state = await _route_to_role_menu_wa(
                    user, db, state_manager
                )

            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard
            )
            responses.append(
                {"from": sender_id, "response": response.text, "new_state": new_state}
            )
            continue

        # אם המשתמש באמצע זרימת בעל תחנה - ממשיכים
        if current_state and current_state.startswith("STATION."):
            from app.domain.services.station_service import StationService

            station_service = StationService(db)
            station = await station_service.get_station_by_owner(user.id)

            if station:
                handler = StationOwnerStateHandler(db, station.id, platform="whatsapp")
                response, new_state = await handler.handle_message(
                    user, text, photo_file_id
                )
            else:
                # תחנה לא נמצאה - fallback
                response, new_state = await _route_to_role_menu_wa(
                    user, db, state_manager
                )

            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard
            )
            responses.append(
                {"from": sender_id, "response": response.text, "new_state": new_state}
            )
            continue

        # Route based on user role
        if user.role == UserRole.COURIER:
            # שמירת המצב הקודם לפני הטיפול בהודעה
            previous_state = current_state

            handler = CourierStateHandler(db, platform="whatsapp")
            response, new_state = await handler.handle_message(
                user, text, photo_file_id
            )

            # שליחת "כרטיס נהג" למנהלים רק במעבר הראשון למצב PENDING_APPROVAL
            if (
                new_state == CourierState.PENDING_APPROVAL.value
                and previous_state != CourierState.PENDING_APPROVAL.value
                and user.approval_status == ApprovalStatus.PENDING
            ):
                # שליפת מסמכים ישירות מה-DB - כל השדות כבר נשמרו בשלבי ה-KYC
                background_tasks.add_task(
                    AdminNotificationService.notify_new_courier_registration,
                    user.id,
                    user.full_name or user.name or "לא צוין",
                    user.service_area or "לא צוין",
                    user.phone_number,
                    user.id_document_url,
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
                        photo_file_id,
                    )

            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard
            )
            responses.append(
                {"from": sender_id, "response": response.text, "new_state": new_state}
            )
            continue

        # Sender flow
        if "שלוח" in text or "חבילה" in text:
            handler = SenderStateHandler(db)
            response, new_state = await handler.handle_message(
                user_id=user.id, platform="whatsapp", message=text
            )

            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard
            )
            responses.append(
                {"from": sender_id, "response": response.text, "new_state": new_state}
            )
            continue

        # If user is in the middle of a sender flow, continue it
        if (
            current_state
            and not current_state.startswith("COURIER.")
            and not current_state.startswith("DISPATCHER.")
            and not current_state.startswith("STATION.")
            and current_state not in ["INITIAL", "SENDER.INITIAL"]
        ):
            handler = SenderStateHandler(db)
            response, new_state = await handler.handle_message(
                user_id=user.id, platform="whatsapp", message=text
            )

            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard
            )
            responses.append(
                {"from": sender_id, "response": response.text, "new_state": new_state}
            )
            continue

        # Default: show welcome message with role selection
        background_tasks.add_task(send_welcome_message, reply_to)
        responses.append({"from": sender_id, "response": "welcome", "new_state": None})

    return {"processed": len(responses), "responses": responses}


@router.get(
    "/webhook",
    summary="Webhook Verification - WhatsApp",
    description="אימות webhook (challenge) עבור WhatsApp Business API.",
)
async def whatsapp_verify(
    hub_mode: str = None, hub_challenge: str = None, hub_verify_token: str = None
):
    """Webhook verification for WhatsApp Business API"""
    if hub_mode == "subscribe" and hub_challenge:
        return int(hub_challenge)
    return {"status": "ok"}
