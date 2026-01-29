"""
WhatsApp Webhook Handler - Bot Gateway Layer
"""
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User, UserRole
from app.state_machine.handlers import SenderStateHandler

router = APIRouter()


class WhatsAppMessage(BaseModel):
    """Incoming WhatsApp message structure"""
    from_number: str
    message_id: str
    text: str
    timestamp: int


class WhatsAppWebhookPayload(BaseModel):
    """WhatsApp webhook payload"""
    messages: list[WhatsAppMessage] = []


async def get_or_create_user(
    db: AsyncSession,
    phone_number: str
) -> User:
    """Get existing user or create new one"""
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

    return user


async def send_whatsapp_message(phone_number: str, text: str, keyboard: list = None):
    """
    Send message via WhatsApp Gateway (Node.js microservice).
    In production, this would make HTTP call to the WPPConnect gateway.
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
        # Log error but don't fail - message will be in outbox for retry
        print(f"WhatsApp send failed: {e}")


@router.post("/webhook")
async def whatsapp_webhook(
    payload: WhatsAppWebhookPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle incoming WhatsApp messages.
    This is the Bot Gateway layer entry point.
    """
    responses = []

    for message in payload.messages:
        # Get or create user
        user = await get_or_create_user(db, message.from_number)

        # Process through state machine
        handler = SenderStateHandler(db)
        response, new_state = await handler.handle_message(
            user_id=user.id,
            platform="whatsapp",
            message=message.text
        )

        # Queue response to be sent
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

    return {"processed": len(responses), "responses": responses}


@router.get("/webhook")
async def whatsapp_verify(
    hub_mode: str = None,
    hub_challenge: str = None,
    hub_verify_token: str = None
):
    """Webhook verification for WhatsApp Business API"""
    # In production, verify the token matches your configured token
    if hub_mode == "subscribe" and hub_challenge:
        return int(hub_challenge)
    return {"status": "ok"}
