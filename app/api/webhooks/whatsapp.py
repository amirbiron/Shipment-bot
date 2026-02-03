"""
WhatsApp Webhook Handler - Bot Gateway Layer
"""
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
    """
    import httpx
    from app.core.config import settings

    # המרת תגי HTML לפורמט וואטסאפ (לדוגמה: <b> -> *)
    formatted_text = convert_html_to_whatsapp(text)

    circuit_breaker = get_whatsapp_circuit_breaker()

    async def _send():
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.WHATSAPP_GATEWAY_URL}/send",
                json={
                    "phone": phone_number,
                    "message": formatted_text,
                    "keyboard": keyboard
                },
                timeout=30.0
            )
            if response.status_code != 200:
                raise Exception(f"WhatsApp API returned {response.status_code}")

    try:
        await circuit_breaker.execute(_send)
    except Exception as e:
        logger.error(
            "WhatsApp send failed",
            extra_data={"phone": PhoneNumberValidator.mask(phone_number), "error": str(e)},
            exc_info=True
        )


async def send_welcome_message(phone_number: str):
    """Send initial welcome message with role selection [1.1]"""
    welcome_text = """שלום וברוכים הבאים! 👋

אני הבוט של *משלוח בצ'יק*.

מה תרצה לעשות?

בכל שלב תוכלו לחזור לתפריט הראשי על ידי הקשה של #

1️⃣ אני רוצה לשלוח חבילה
2️⃣ אני שליח"""

    keyboard = [
        ["📦 אני רוצה לשלוח חבילה"],
        ["🚚 אני שליח"]
    ]
    await send_whatsapp_message(phone_number, welcome_text, keyboard)


@router.post("/webhook")
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
            # Reset state to menu
            if user.role == UserRole.COURIER:
                # בדיקה אם השליח לא מאושר (כולל None, PENDING, REJECTED, BLOCKED)
                # אפשר לו לחזור להיות שולח רגיל
                if user.approval_status != ApprovalStatus.APPROVED:
                    # מחזירים אותו להיות שולח רגיל
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

        # Check if user wants to be a courier [1.1]
        if "שליח" in text and user.role == UserRole.SENDER:
            # Switch to courier role and start registration
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

        # Route based on user role
        if user.role == UserRole.COURIER:
            handler = CourierStateHandler(db, platform="whatsapp")
            response, new_state = await handler.handle_message(user, text, photo_file_id)

            # Check if courier just completed registration - notify admin [1.4]
            if new_state == CourierState.PENDING_APPROVAL.value and user.approval_status == ApprovalStatus.PENDING:
                context = await state_manager.get_context(user.id, "whatsapp")
                background_tasks.add_task(
                    AdminNotificationService.notify_new_courier_registration,
                    user.id,
                    user.full_name or user.name or "לא צוין",
                    user.service_area or "לא צוין",
                    user.phone_number,
                    context.get("document_file_id")
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


@router.get("/webhook")
async def whatsapp_verify(
    hub_mode: str = None,
    hub_challenge: str = None,
    hub_verify_token: str = None
):
    """Webhook verification for WhatsApp Business API"""
    if hub_mode == "subscribe" and hub_challenge:
        return int(hub_challenge)
    return {"status": "ok"}
