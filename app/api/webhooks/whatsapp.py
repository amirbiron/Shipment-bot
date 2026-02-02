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

logger = get_logger(__name__)

router = APIRouter()


class WhatsAppMessage(BaseModel):
    """Incoming WhatsApp message structure"""
    from_number: str
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
    phone_number: str
) -> tuple[User, bool]:
    """Get existing user or create new one. Returns (user, is_new)"""
    result = await db.execute(
        select(User).where(User.phone_number == phone_number)
    )
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            phone_number=phone_number,
            platform="whatsapp",
            role=UserRole.SENDER
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user, True  # New user

    return user, False  # Existing user


async def send_whatsapp_message(phone_number: str, text: str, keyboard: list = None):
    """
    Send message via WhatsApp Gateway (Node.js microservice).
    """
    import httpx
    from app.core.config import settings

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.WHATSAPP_GATEWAY_URL}/send",
                json={
                    "phone": phone_number,
                    "message": text,
                    "keyboard": keyboard
                },
                timeout=30.0
            )
    except Exception as e:
        logger.error(
            "WhatsApp send failed",
            extra_data={"phone": phone_number[-4:] + "****", "error": str(e)},
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
        # Accept image media (WPPConnect may return 'image' or have image in mimetype)
        photo_file_id = message.media_url if message.media_type and 'image' in message.media_type.lower() else None

        logger.debug(
            "WhatsApp message received",
            extra_data={
                "from": message.from_number[-4:] + "****",
                "text_preview": text[:50] if text else "",
                "media_type": message.media_type,
                "has_media_url": bool(message.media_url)
            }
        )

        # Skip empty messages
        if not text and not photo_file_id:
            continue

        # Get or create user
        user, is_new_user = await get_or_create_user(db, message.from_number)

        # Initialize state manager
        state_manager = StateManager(db)

        # New user - show welcome message with role selection [1.1]
        if is_new_user:
            background_tasks.add_task(send_welcome_message, message.from_number)
            responses.append({
                "from": message.from_number,
                "response": "welcome",
                "new_user": True
            })
            continue

        # Handle "#" to return to main menu
        if text.strip() == "#":
            # Reset state to menu
            if user.role == UserRole.COURIER:
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
                message.from_number,
                response.text,
                response.keyboard
            )
            responses.append({
                "from": message.from_number,
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
                message.from_number,
                response.text,
                response.keyboard
            )
            responses.append({
                "from": message.from_number,
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
                message.from_number,
                response.text,
                response.keyboard
            )
            responses.append({
                "from": message.from_number,
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
                message.from_number,
                response.text,
                response.keyboard
            )
            responses.append({
                "from": message.from_number,
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
                message.from_number,
                response.text,
                response.keyboard
            )
            responses.append({
                "from": message.from_number,
                "response": response.text,
                "new_state": new_state
            })
            continue

        # Default: show welcome message with role selection
        background_tasks.add_task(send_welcome_message, message.from_number)
        responses.append({
            "from": message.from_number,
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
