"""
Telegram Webhook Handler - Bot Gateway Layer
"""
import hashlib
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User, UserRole, ApprovalStatus
from app.state_machine.handlers import SenderStateHandler, CourierStateHandler
from app.state_machine.states import CourierState
from app.state_machine.manager import StateManager
from app.domain.services import AdminNotificationService
from app.core.logging import get_logger
from app.core.circuit_breaker import get_telegram_circuit_breaker
from app.core.exceptions import TelegramError

logger = get_logger(__name__)

router = APIRouter()

def _telegram_phone_placeholder(telegram_chat_id: str) -> str:
    """
    יצירת placeholder קצר ל-phone_number עבור משתמשי Telegram.

    חלק מהסביבות (למשל DB בפרודקשן) מגדירות phone_number כ-NOT NULL,
    למרות שבטלגרם אין מספר טלפון אמין בשלב ה-webhook.
    """
    candidate = f"tg:{telegram_chat_id}"
    if len(candidate) <= 20:
        return candidate
    digest = hashlib.sha1(telegram_chat_id.encode("utf-8")).hexdigest()[:17]
    return f"tg:{digest}"


class TelegramUser(BaseModel):
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None


class TelegramChat(BaseModel):
    id: int
    type: str


class TelegramPhotoSize(BaseModel):
    file_id: str
    file_unique_id: str
    width: int
    height: int


class TelegramMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int
    from_user: Optional[TelegramUser] = Field(default=None, alias="from")
    chat: TelegramChat
    text: Optional[str] = None
    photo: Optional[List[TelegramPhotoSize]] = None
    date: int


class TelegramCallbackQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_user: Optional[TelegramUser] = Field(default=None, alias="from")
    message: Optional[TelegramMessage] = None
    data: Optional[str] = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None
    callback_query: Optional[TelegramCallbackQuery] = None


async def get_or_create_user(
    db: AsyncSession,
    telegram_chat_id: str,
    name: Optional[str] = None
) -> tuple[User, bool]:
    """Get existing user or create new one. Returns (user, is_new)"""
    result = await db.execute(
        select(User).where(User.telegram_chat_id == telegram_chat_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            # הערה: שומר placeholder כדי למנוע כשלי DB כש-phone_number מוגדר NOT NULL
            phone_number=_telegram_phone_placeholder(telegram_chat_id),
            telegram_chat_id=telegram_chat_id,
            name=name,
            platform="telegram",
            role=UserRole.SENDER
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user, True  # New user

    return user, False  # Existing user


async def send_telegram_message(
    chat_id: str,
    text: str,
    keyboard: Optional[list] = None,
    inline: bool = False
) -> None:
    """Send message via Telegram Bot API with circuit breaker protection"""
    import httpx
    from app.core.config import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram bot token not configured")
        return

    circuit_breaker = get_telegram_circuit_breaker()

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    if keyboard:
        if inline:
            # Convert keyboard to inline keyboard format
            inline_keyboard = []
            for row in keyboard:
                inline_row = []
                for button_text in row:
                    inline_row.append({
                        "text": button_text,
                        "callback_data": button_text
                    })
                inline_keyboard.append(inline_row)
            payload["reply_markup"] = {
                "inline_keyboard": inline_keyboard
            }
        else:
            payload["reply_markup"] = {
                "keyboard": keyboard,
                "resize_keyboard": True,
                "one_time_keyboard": True
            }

    async def _send():
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30.0)
            if response.status_code != 200:
                raise TelegramError.from_response(
                    "sendMessage",
                    response,
                    message=f"sendMessage returned status {response.status_code}",
                )

    try:
        await circuit_breaker.execute(_send)
    except Exception as e:
        logger.error(
            "Telegram send failed",
            extra_data={"chat_id": chat_id, "error": str(e)},
            exc_info=True
        )


async def answer_callback_query(callback_query_id: str, text: str = None) -> None:
    """Answer callback query to remove loading state with circuit breaker protection"""
    import httpx
    from app.core.config import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        return

    circuit_breaker = get_telegram_circuit_breaker()

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text

    async def _send():
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30.0)
            if response.status_code != 200:
                raise TelegramError.from_response(
                    "answerCallbackQuery",
                    response,
                    message=f"answerCallbackQuery returned status {response.status_code}",
                )

    try:
        await circuit_breaker.execute(_send)
    except Exception as e:
        logger.error(
            "Answer callback failed",
            extra_data={"callback_query_id": callback_query_id, "error": str(e)},
            exc_info=True
        )


async def send_welcome_message(chat_id: str):
    """Send initial welcome message with role selection [1.1]"""
    welcome_text = """שלום וברוכים הבאים! 👋

אני הבוט של <b>משלוח בצ'יק</b>.

מה תרצה לעשות?"""
    keyboard = [
        ["📦 אני רוצה לשלוח חבילה"],
        ["🚚 אני שליח"]
    ]
    await send_telegram_message(chat_id, welcome_text, keyboard, inline=True)


@router.post("/webhook")
async def telegram_webhook(
    update: TelegramUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle incoming Telegram messages.
    This is the Bot Gateway layer entry point for Telegram.
    Routes to sender or courier handlers based on user role.
    """
    # Handle callback queries (inline button clicks)
    if update.callback_query:
        callback = update.callback_query
        chat_id = str(callback.message.chat.id) if callback.message else None
        text = callback.data or ""
        photo_file_id = None

        # Answer the callback query to remove loading state
        background_tasks.add_task(answer_callback_query, callback.id)

        # Get user name if available
        name = None
        if callback.from_user:
            name = callback.from_user.first_name
            if callback.from_user.last_name:
                name += f" {callback.from_user.last_name}"
    elif update.message:
        message = update.message
        chat_id = str(message.chat.id)

        # Handle text or photo messages
        text = message.text or ""
        photo_file_id = None
        if message.photo:
            # Get largest photo (last in list)
            photo_file_id = message.photo[-1].file_id

        # Get user name if available
        name = None
        if message.from_user:
            name = message.from_user.first_name
            if message.from_user.last_name:
                name += f" {message.from_user.last_name}"
    else:
        return {"ok": True}

    # Skip if no content
    if not text and not photo_file_id:
        return {"ok": True}

    # Get or create user
    user, is_new_user = await get_or_create_user(db, chat_id, name)

    # Initialize state manager
    state_manager = StateManager(db)

    # New user - show welcome message with role selection [1.1]
    if is_new_user:
        background_tasks.add_task(send_welcome_message, chat_id)
        return {"ok": True, "new_user": True}

    # טיפול ב-/start בכל שלב: איפוס ההקשר וחזרה לנקודת כניסה בטוחה
    if update.message and text.strip().startswith("/start"):
        if user.role == UserRole.COURIER:
            await db.refresh(user)
            if user.approval_status == ApprovalStatus.APPROVED:
                await state_manager.force_state(user.id, "telegram", CourierState.MENU.value, context={})
            else:
                await state_manager.force_state(user.id, "telegram", CourierState.INITIAL.value, context={})

            handler = CourierStateHandler(db)
            response, new_state = await handler.handle_message(user, "תפריט", None)
        else:
            from app.state_machine.states import SenderState

            await state_manager.force_state(user.id, "telegram", SenderState.MENU.value, context={})
            handler = SenderStateHandler(db)
            response, new_state = await handler.handle_message(
                user_id=user.id,
                platform="telegram",
                message="תפריט"
            )

        background_tasks.add_task(
            send_telegram_message,
            chat_id,
            response.text,
            response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state, "reset": True}

    # Handle "#" to return to main menu
    if text.strip() == "#":
        # Reset state to menu
        if user.role == UserRole.COURIER:
            await state_manager.force_state(user.id, "telegram", CourierState.MENU.value, context={})
            handler = CourierStateHandler(db)
            response, new_state = await handler.handle_message(user, "תפריט", None)
        else:
            from app.state_machine.states import SenderState
            await state_manager.force_state(user.id, "telegram", SenderState.MENU.value, context={})
            handler = SenderStateHandler(db)
            response, new_state = await handler.handle_message(
                user_id=user.id,
                platform="telegram",
                message="תפריט"
            )

        background_tasks.add_task(
            send_telegram_message,
            chat_id,
            response.text,
            response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # Check if user wants to be a courier [1.1]
    if "שליח" in text and user.role == UserRole.SENDER:
        # Switch to courier role and start registration
        user.role = UserRole.COURIER
        await db.commit()

        await state_manager.force_state(
            user.id, "telegram",
            CourierState.INITIAL.value,
            context={}
        )

        handler = CourierStateHandler(db)
        response, new_state = await handler.handle_message(user, text, photo_file_id)

        background_tasks.add_task(
            send_telegram_message,
            chat_id,
            response.text,
            response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # Route based on user role
    if user.role == UserRole.COURIER:
        # שמירת המצב הקודם לפני הטיפול בהודעה
        previous_state = await state_manager.get_current_state(user.id, "telegram")

        handler = CourierStateHandler(db)
        response, new_state = await handler.handle_message(user, text, photo_file_id)

        # שליחת התראה למנהלים רק במעבר הראשון למצב PENDING_APPROVAL
        # (כלומר רק כשהמצב הקודם היה שונה - למניעת שליחה כפולה)
        if (new_state == CourierState.PENDING_APPROVAL.value and
            previous_state != CourierState.PENDING_APPROVAL.value and
            user.approval_status == ApprovalStatus.PENDING):
            context = await state_manager.get_context(user.id, "telegram")
            background_tasks.add_task(
                AdminNotificationService.notify_new_courier_registration,
                user.id,
                user.full_name or user.name or "לא צוין",
                user.service_area or "לא צוין",
                user.telegram_chat_id,
                context.get("document_file_id")
            )

        # Check if courier submitted deposit screenshot
        if photo_file_id:
            context = await state_manager.get_context(user.id, "telegram")
            if context.get("deposit_screenshot"):
                background_tasks.add_task(
                    AdminNotificationService.notify_deposit_request,
                    user.id,
                    user.full_name or user.name or "לא ידוע",
                    user.telegram_chat_id,
                    photo_file_id
                )

        background_tasks.add_task(
            send_telegram_message,
            chat_id,
            response.text,
            response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # Sender flow
    if "שלוח" in text or "חבילה" in text:
        handler = SenderStateHandler(db)
        response, new_state = await handler.handle_message(
            user_id=user.id,
            platform="telegram",
            message=text
        )

        background_tasks.add_task(
            send_telegram_message,
            chat_id,
            response.text,
            response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # Check current state for senders
    current_state = await state_manager.get_current_state(user.id, "telegram")

    # If user is in the middle of a sender flow, continue it
    if current_state and not current_state.startswith("COURIER.") and current_state not in ["INITIAL", "SENDER.INITIAL"]:
        handler = SenderStateHandler(db)
        response, new_state = await handler.handle_message(
            user_id=user.id,
            platform="telegram",
            message=text
        )

        background_tasks.add_task(
            send_telegram_message,
            chat_id,
            response.text,
            response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # Default: show welcome message with role selection
    background_tasks.add_task(send_welcome_message, chat_id)
    return {"ok": True}
