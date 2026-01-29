"""
Telegram Webhook Handler - Bot Gateway Layer
"""
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User, UserRole
from app.state_machine.handlers import SenderStateHandler

router = APIRouter()


class TelegramUser(BaseModel):
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None


class TelegramChat(BaseModel):
    id: int
    type: str


class TelegramMessage(BaseModel):
    message_id: int
    from_user: Optional[TelegramUser] = None
    chat: TelegramChat
    text: Optional[str] = None
    date: int

    class Config:
        populate_by_name = True
        # Handle 'from' field which is reserved in Python
        fields = {'from_user': 'from'}


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None


async def get_or_create_user(
    db: AsyncSession,
    telegram_chat_id: str,
    name: Optional[str] = None
) -> User:
    """Get existing user or create new one"""
    result = await db.execute(
        select(User).where(User.telegram_chat_id == telegram_chat_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            phone_number=f"tg_{telegram_chat_id}",  # Placeholder
            telegram_chat_id=telegram_chat_id,
            name=name,
            platform="telegram",
            role=UserRole.SENDER
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user


async def send_telegram_message(
    chat_id: str,
    text: str,
    keyboard: Optional[list] = None
):
    """Send message via Telegram Bot API"""
    import httpx
    from app.core.config import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        print("Telegram bot token not configured")
        return

    try:
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        if keyboard:
            payload["reply_markup"] = {
                "keyboard": keyboard,
                "resize_keyboard": True,
                "one_time_keyboard": True
            }

        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=30.0)
    except Exception as e:
        print(f"Telegram send failed: {e}")


@router.post("/webhook")
async def telegram_webhook(
    update: TelegramUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle incoming Telegram messages.
    This is the Bot Gateway layer entry point for Telegram.
    """
    if not update.message or not update.message.text:
        return {"ok": True}

    message = update.message
    chat_id = str(message.chat.id)

    # Get user name if available
    name = None
    if message.from_user:
        name = message.from_user.first_name
        if message.from_user.last_name:
            name += f" {message.from_user.last_name}"

    # Get or create user
    user = await get_or_create_user(db, chat_id, name)

    # Process through state machine
    handler = SenderStateHandler(db)
    response, new_state = await handler.handle_message(
        user_id=user.id,
        platform="telegram",
        message=message.text
    )

    # Queue response to be sent
    background_tasks.add_task(
        send_telegram_message,
        chat_id,
        response.text,
        response.keyboard
    )

    return {
        "ok": True,
        "response": response.text,
        "new_state": new_state
    }
