"""
Telegram Webhook Handler - Bot Gateway Layer
"""
import re
import hashlib
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, TypeAlias
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.state_machine.handlers import SenderStateHandler, CourierStateHandler, MessageResponse
from app.state_machine.states import CourierState, DispatcherState, StationOwnerState, SenderState
from app.state_machine.dispatcher_handler import DispatcherStateHandler
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager
from app.domain.services import AdminNotificationService
from app.domain.services.courier_approval_service import CourierApprovalService
from app.core.logging import get_logger
from app.core.circuit_breaker import get_telegram_circuit_breaker
from app.core.config import settings
from app.core.exceptions import TelegramError

logger = get_logger(__name__)

router = APIRouter()

_SenderButtonHandler: TypeAlias = Callable[
    [User, AsyncSession, StateManager, str, str | None],
    Awaitable[tuple[MessageResponse, str | None]],
]


@dataclass(frozen=True)
class _InboundTelegramEvent:
    """אירוע נכנס מנורמל מה-update של טלגרם"""

    # send_chat_id: לאן שולחים את התשובה (private chat / קבוצה)
    send_chat_id: str | None
    # telegram_user_id: מזהה המשתמש שביצע את הפעולה (מי כתב/לחץ) - לצורך זיהוי המשתמש במערכת
    telegram_user_id: str | None
    text: str
    photo_file_id: str | None
    name: str | None
    is_callback: bool
    callback_query_id: str | None


def _queue_response_send(
    background_tasks: BackgroundTasks,
    chat_id: str,
    response: MessageResponse,
) -> None:
    """שולח תגובה למשתמש דרך background task"""
    background_tasks.add_task(
        send_telegram_message,
        chat_id,
        response.text,
        response.keyboard,
        getattr(response, "inline", False),
    )


def _parse_inbound_event(
    update: "TelegramUpdate",
    background_tasks: BackgroundTasks,
) -> _InboundTelegramEvent | None:
    """נרמול update לאירוע אחיד (טקסט/תמונה/כפתור)."""
    if update.callback_query:
        callback = update.callback_query
        if callback.from_user is None:
            # נענה ל-callback כדי להסיר loading, אבל נדלג על עיבוד ללא מזהה משתמש אמין
            background_tasks.add_task(answer_callback_query, callback.id)
            logger.warning(
                "Telegram callback_query without from_user; skipping processing",
                extra_data={"callback_query_id": callback.id},
            )
            return None

        # חשוב: זיהוי משתמש לפי from_user.id (מי לחץ), לא לפי chat.id (איפה ההודעה)
        telegram_user_id = str(callback.from_user.id)
        send_chat_id = _resolve_telegram_chat_id(update)
        text = callback.data or ""

        # Answer the callback query to remove loading state
        background_tasks.add_task(answer_callback_query, callback.id)

        name = callback.from_user.first_name
        if callback.from_user.last_name:
            name += f" {callback.from_user.last_name}"

        return _InboundTelegramEvent(
            send_chat_id=send_chat_id,
            telegram_user_id=telegram_user_id,
            text=text,
            photo_file_id=None,
            name=name,
            is_callback=True,
            callback_query_id=callback.id,
        )

    if update.message:
        message = update.message
        send_chat_id = str(message.chat.id)
        # תאימות אחורה + נכונות:
        # - ב-private chat: מזהה הצ'אט הוא מזהה המשתמש ולכן נשמור לפי chat.id
        #   (גם אם ה-payload בבדיקות לא עקבי בין chat.id ל-from.id)
        # - בקבוצות/ערוצים: חייב לזהות משתמש לפי from_user.id (מי כתב)
        if message.chat and message.chat.type == "private":
            telegram_user_id = send_chat_id
        else:
            telegram_user_id = str(message.from_user.id) if message.from_user else send_chat_id
        text = message.text or ""

        photo_file_id = None
        if message.photo:
            # תמונה דחוסה - לוקחים את הגודל הגדול ביותר
            photo_file_id = message.photo[-1].file_id
        elif (
            message.document
            and message.document.mime_type
            and message.document.mime_type.lower().startswith("image/")
        ):
            # קובץ תמונה שנשלח כמסמך (לא דחוס)
            photo_file_id = message.document.file_id

        name = None
        if message.from_user:
            name = message.from_user.first_name
            if message.from_user.last_name:
                name += f" {message.from_user.last_name}"

        return _InboundTelegramEvent(
            send_chat_id=send_chat_id,
            telegram_user_id=telegram_user_id,
            text=text,
            photo_file_id=photo_file_id,
            name=name,
            is_callback=False,
            callback_query_id=None,
        )

    return None


def _is_courier_in_registration_state(
    user: User,
    current_state: str | None,
) -> bool:
    if user.role != UserRole.COURIER or not current_state:
        return False

    return current_state in {
        CourierState.REGISTER_COLLECT_NAME.value,
        CourierState.REGISTER_COLLECT_DOCUMENT.value,
        CourierState.REGISTER_COLLECT_SELFIE.value,
        CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value,
        CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value,
        CourierState.REGISTER_TERMS.value,
    }


def _is_in_multi_step_flow(
    user: User,
    current_state: str | None,
) -> bool:
    """Guard אחיד: לא ליירט כפתורי תפריט בזמן זרימה רב-שלבית."""
    if _is_courier_in_registration_state(user, current_state):
        return True

    # הגנה על זרימות שולח: מונע "תחנה" וכו' מלתפוס כתובות כמו "תחנה מרכזית"
    if (
        isinstance(current_state, str)
        and current_state.startswith("SENDER.")
        and current_state != SenderState.MENU.value
    ):
        return True

    if isinstance(current_state, str) and current_state.startswith(("DISPATCHER.", "STATION.")):
        return True

    return False


async def _get_station_for_owner_or_downgrade(
    user: User,
    db: AsyncSession,
) -> Station | None:
    """שליפת תחנה לבעל תחנה; אם אין תחנה פעילה מוריד תפקיד לשולח."""
    from app.domain.services.station_service import StationService

    station_service = StationService(db)
    station = await station_service.get_station_by_owner(user.id)
    if station:
        return station

    # בעל תחנה ללא תחנה פעילה - הורדת תפקיד לשולח כדי למנוע לולאה אינסופית
    logger.warning(
        "Station owner without active station, downgrading to sender",
        extra_data={"user_id": user.id},
    )
    user.role = UserRole.SENDER
    await db.commit()
    return None


async def _get_dispatcher_station(
    user: User,
    db: AsyncSession,
) -> Station | None:
    """שליפת תחנה לסדרן (נהג)."""
    from app.domain.services.station_service import StationService

    station_service = StationService(db)
    return await station_service.get_dispatcher_station(user.id)


async def _handle_sender_join_as_courier(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
    text: str,
    photo_file_id: str | None,
) -> tuple[MessageResponse, str]:
    """ניתוב לתהליך רישום כשליח מתוך תפריט שולח."""
    user.role = UserRole.COURIER
    await db.commit()

    await state_manager.force_state(user.id, "telegram", CourierState.INITIAL.value, context={})
    handler = CourierStateHandler(db)
    response, new_state = await handler.handle_message(user, text, photo_file_id)
    return response, new_state


async def _handle_sender_fast_shipment() -> MessageResponse:
    """קישור חיצוני לקבוצת WhatsApp - העלאת משלוח מהיר."""
    if settings.WHATSAPP_GROUP_LINK:
        msg_text = (
            "📦 <b>העלאת משלוח מהיר</b>\n\n"
            "להעלאת משלוח מהיר, הצטרפו לקבוצת WhatsApp שלנו:\n"
            f"{settings.WHATSAPP_GROUP_LINK}"
        )
    else:
        msg_text = (
            "📦 <b>העלאת משלוח מהיר</b>\n\n"
            "להעלאת משלוח מהיר, פנו להנהלה לקבלת קישור לקבוצת WhatsApp."
        )
    return MessageResponse(msg_text)


def _static_sender_button(
    response_factory: Callable[[], Awaitable[MessageResponse]],
) -> _SenderButtonHandler:
    """אדפטר לכפתורי שולח שלא צריכים את פרטי הבקשה."""

    async def _handler(
        user: User,
        db: AsyncSession,
        state_manager: StateManager,
        text: str,
        photo_file_id: str | None,
    ) -> tuple[MessageResponse, str | None]:
        del user, db, state_manager, text, photo_file_id
        resp = await response_factory()
        return resp, None

    return _handler


async def _handle_sender_station_signup() -> MessageResponse:
    """הודעה שיווקית עבור תחנות."""
    station_text = (
        "🏪 <b>הצטרפות כתחנה</b>\n\n"
        "המערכת של ShipShare מסדרת לך את התחנה!\n\n"
        "✅ ניהול נהגים אוטומטי\n"
        "✅ גבייה מסודרת\n"
        "✅ תיעוד משלוחים מלא\n"
        "✅ סדר בבלגן\n\n"
        "לפרטים נוספים, פנו להנהלה."
    )
    return MessageResponse(station_text, keyboard=[["📞 פנייה לניהול"]], inline=True)


async def _handle_sender_admin_contact() -> MessageResponse:
    """קישור WhatsApp ישיר למנהל הראשי (או fallback להודעה בתוך הבוט)."""
    if settings.ADMIN_WHATSAPP_NUMBER:
        admin_link = f"https://wa.me/{settings.ADMIN_WHATSAPP_NUMBER}"
        admin_text = (
            "📞 <b>פנייה לניהול</b>\n\n"
            f"ליצירת קשר עם המנהל:\n{admin_link}"
        )
    else:
        admin_text = (
            "📞 <b>פנייה לניהול</b>\n\n"
            "ליצירת קשר עם המנהל, שלחו הודעה כאן ונחזור אליכם בהקדם."
        )
    return MessageResponse(admin_text)


_sender_button_fast_shipment = _static_sender_button(_handle_sender_fast_shipment)
_sender_button_station_signup = _static_sender_button(_handle_sender_station_signup)
_sender_button_admin_contact = _static_sender_button(_handle_sender_admin_contact)


_SENDER_BUTTON_ROUTES: list[tuple[str, _SenderButtonHandler]] = [
    # חשוב: המיפוי הוא `keyword in text` ולכן **הסדר כאן קריטי**.
    # יש לשים מחרוזות ספציפיות לפני כלליות (למשל "הצטרפות למנוי" לפני "שליח").
    #
    # הצטרפות כשליח (שני keywords כדי לשמור על ההתנהגות הקיימת)
    ("הצטרפות למנוי", _handle_sender_join_as_courier),
    ("שליח", _handle_sender_join_as_courier),
    ("העלאת משלוח מהיר", _sender_button_fast_shipment),
    ("משלוח מהיר", _sender_button_fast_shipment),
    ("הצטרפות כתחנה", _sender_button_station_signup),
    ("תחנה", _sender_button_station_signup),
    ("פנייה לניהול", _sender_button_admin_contact),
]


def _telegram_phone_placeholder(telegram_chat_id: str) -> str:
    """
    יצירת placeholder קצר ל-phone_number עבור משתמשי Telegram.

    חלק מהסביבות (למשל DB בפרודקשן) מגדירות phone_number כ-NOT NULL,
    למרות שבטלגרם אין מספר טלפון אמין בשלב ה-webhook.
    """
    if telegram_chat_id is None or str(telegram_chat_id).strip() in ("", "None"):
        raise ValueError("telegram_chat_id is required for telegram phone placeholder")

    telegram_chat_id = str(telegram_chat_id).strip()
    candidate = f"tg:{telegram_chat_id}"
    if len(candidate) <= 20:
        return candidate
    digest = hashlib.sha1(telegram_chat_id.encode("utf-8")).hexdigest()[:17]
    return f"tg:{digest}"


def _resolve_telegram_chat_id(update: "TelegramUpdate") -> str | None:
    """
    ניסיון לחלץ chat_id יציב גם עבור callback_query ללא message.

    ב-private chat, user_id == chat_id ולכן אפשר ליפול ל-from_user.id.
    """
    if update.message:
        return str(update.message.chat.id)

    if update.callback_query:
        cb = update.callback_query
        if cb.message:
            return str(cb.message.chat.id)
        if cb.from_user:
            return str(cb.from_user.id)

    return None


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


class TelegramDocument(BaseModel):
    """מודל לקבצים/מסמכים שנשלחים בטלגרם (לא כתמונה דחוסה)"""
    file_id: str
    file_unique_id: str
    file_name: Optional[str] = None
    mime_type: Optional[str] = None


class TelegramMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int
    from_user: Optional[TelegramUser] = Field(default=None, alias="from")
    chat: TelegramChat
    text: Optional[str] = None
    photo: Optional[List[TelegramPhotoSize]] = None
    document: Optional[TelegramDocument] = None
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
    """
    Get existing user or create new one. Returns (user, is_new).

    הגנה לפרודקשן:
    בחלק מהסביבות היסטורית, מסד הנתונים לא כלל UNIQUE אמיתי על telegram_chat_id,
    ולכן עלולות להיות כפילויות. במצב כזה scalar_one_or_none() יזרוק MultipleResultsFound
    ויפיל את ה-webhook. כאן אנחנו בוחרים משתמש דטרמיניסטית וממשיכים.
    """
    result = await db.execute(
        select(User)
        .where(User.telegram_chat_id == telegram_chat_id)
        .order_by(User.is_active.desc(), User.updated_at.desc(), User.created_at.desc())
        .limit(2)
    )
    users = list(result.scalars().all())
    user = users[0] if users else None

    if len(users) > 1:
        logger.error(
            "Duplicate telegram_chat_id detected; using first match to avoid webhook crash",
            extra_data={
                "telegram_chat_id": telegram_chat_id,
                "user_ids": [u.id for u in users],
            },
        )

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
    """הודעת ברוכים הבאים ותפריט ראשי [שלב 1]"""
    welcome_text = (
        "ברוכים הבאים ל<b>משלוח בצ'יק</b> 🚚\n"
        "המערכת החכמה לשיתוף משלוחים.\n\n"
        "איך נוכל לעזור היום?"
    )
    keyboard = [
        ["🚚 הצטרפות למנוי וקבלת משלוחים"],
        ["📦 העלאת משלוח מהיר"],
        ["🏪 הצטרפות כתחנה"],
        ["📞 פנייה לניהול"],
    ]
    await send_telegram_message(chat_id, welcome_text, keyboard, inline=True)


async def _sender_fallback(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
) -> tuple[MessageResponse, str]:
    """fallback לתפריט שולח - משותף לכל ה-fallbacks ב-_route_to_role_menu"""
    await state_manager.force_state(user.id, "telegram", SenderState.MENU.value, context={})
    handler = SenderStateHandler(db)
    return await handler.handle_message(
        user_id=user.id, platform="telegram", message="תפריט"
    )


async def _route_to_role_menu(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
) -> tuple[MessageResponse, str]:
    """
    ניתוב לתפריט הנכון לפי תפקיד המשתמש.

    חובה: כל תפקיד (UserRole) חייב להיות מטופל כאן במפורש.
    אם מוסיפים תפקיד חדש - חובה להוסיף ענף כאן, אחרת ייפול ל-SENDER עם אזהרה בלוג.

    Returns: (response, new_state)
    """
    if user.role == UserRole.COURIER:
        await state_manager.force_state(user.id, "telegram", CourierState.MENU.value, context={})
        handler = CourierStateHandler(db)
        return await handler.handle_message(user, "תפריט", None)

    if user.role == UserRole.STATION_OWNER:
        station = await _get_station_for_owner_or_downgrade(user, db)
        if station is not None:
            await state_manager.force_state(
                user.id, "telegram",
                StationOwnerState.MENU.value,
                context={}
            )
            handler = StationOwnerStateHandler(db, station.id)
            return await handler.handle_message(user, "תפריט", None)
        return await _sender_fallback(user, db, state_manager)

    if user.role == UserRole.SENDER or user.role == UserRole.ADMIN:
        # ADMIN מנוהל דרך ממשק אחר - בבוט מקבל תפריט שולח
        return await _sender_fallback(user, db, state_manager)

    # תפקיד לא מוכר - אזהרה בלוג ו-fallback לשולח
    logger.warning(
        "Unknown user role in menu routing, falling back to sender",
        extra_data={"user_id": user.id, "role": str(user.role)}
    )
    return await _sender_fallback(user, db, state_manager)


@router.post(
    "/webhook",
    summary="Webhook - Telegram (קבלת עדכונים נכנסים)",
    description=(
        "נקודת כניסה לקבלת עדכונים מ-Telegram Bot API. "
        "תומכת גם בהודעות טקסט/תמונות וגם ב-callback queries (כפתורים)."
    ),
)
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
    event = _parse_inbound_event(update, background_tasks)
    if event is None:
        return {"ok": True}

    send_chat_id = event.send_chat_id
    telegram_user_id = event.telegram_user_id
    text = event.text or ""
    photo_file_id = event.photo_file_id
    name = event.name

    # Skip if no content
    if not text and not photo_file_id:
        return {"ok": True}

    if not send_chat_id or not telegram_user_id:
        logger.warning(
            "Telegram update missing send_chat_id or telegram_user_id; skipping processing",
            extra_data={
                "has_message": bool(update.message),
                "has_callback_query": bool(update.callback_query),
            },
        )
        return {"ok": True}

    # טיפול בכפתורי אישור/דחיית שליח (מנהלים בלבד) - לפני ניתוב רגיל
    if event.is_callback:
        courier_action = re.match(r"^(approve|reject)_courier_(\d+)$", text)
        if courier_action:
            clicker_id = telegram_user_id
            admin_ids = {cid.strip() for cid in settings.TELEGRAM_ADMIN_CHAT_IDS.split(",") if cid.strip()}
            if settings.TELEGRAM_ADMIN_CHAT_ID:
                admin_ids.add(settings.TELEGRAM_ADMIN_CHAT_ID)

            if clicker_id and clicker_id in admin_ids:
                action = courier_action.group(1)
                courier_id = int(courier_action.group(2))
                admin_name = name or "מנהל"

                if action == "approve":
                    result = await CourierApprovalService.approve(db, courier_id)
                else:
                    result = await CourierApprovalService.reject(db, courier_id)

                # שליחת תוצאה למנהל (בצ'אט שבו לחץ)
                background_tasks.add_task(send_telegram_message, send_chat_id, result.message)

                # אם הפעולה הצליחה - הודעה לשליח וסיכום לקבוצה
                if result.success and result.user:
                    from app.api.webhooks.whatsapp import send_whatsapp_message

                    background_tasks.add_task(
                        CourierApprovalService.notify_after_decision,
                        result.user,
                        action,
                        admin_name,
                        send_telegram_fn=send_telegram_message,
                        send_whatsapp_fn=send_whatsapp_message,
                    )

                return {"ok": True, "admin_action": action, "courier_id": courier_id}

            logger.warning(
                "Non-admin clicked approval button",
                extra_data={"clicker_id": clicker_id, "chat_id": send_chat_id},
            )
            return {"ok": True}

    # שלב 4: טיפול בכפתורי אישור/דחיית משלוח (סדרנים בלבד)
    if event.is_callback:
        delivery_action = re.match(r"^(approve|reject)_delivery_(\d+)$", text)
        if delivery_action:
            action = delivery_action.group(1)
            delivery_id = int(delivery_action.group(2))

            # זיהוי הלוחץ
            user, _ = await get_or_create_user(db, telegram_user_id, name)

            # שליפת המשלוח לבדיקת תחנה
            from app.domain.services.station_service import StationService
            station_service = StationService(db)

            from app.db.models.delivery import Delivery
            delivery_result = await db.execute(
                select(Delivery).where(Delivery.id == delivery_id)
            )
            target_delivery = delivery_result.scalar_one_or_none()

            if not target_delivery or not target_delivery.station_id:
                background_tasks.add_task(
                    send_telegram_message, send_chat_id,
                    "❌ המשלוח לא נמצא."
                )
                return {"ok": True}

            # בדיקה שהסדרן שייך לתחנה של המשלוח הספציפי
            is_disp = await station_service.is_dispatcher_of_station(
                user.id, target_delivery.station_id
            )

            if not is_disp:
                background_tasks.add_task(
                    send_telegram_message, send_chat_id,
                    "❌ אין לך הרשאה לאשר/לדחות משלוחים בתחנה זו."
                )
                return {"ok": True}

            from app.domain.services.shipment_workflow_service import ShipmentWorkflowService
            workflow = ShipmentWorkflowService(db)

            try:
                if action == "approve":
                    success, msg, delivery = await workflow.approve_delivery(
                        delivery_id, user.id
                    )
                else:
                    success, msg, delivery = await workflow.reject_delivery(
                        delivery_id, user.id
                    )
            except Exception as e:
                # rollback למניעת שינויים חלקיים (flush ללא commit) שנשארים בסשן
                await db.rollback()
                logger.error(
                    "Delivery approval/rejection failed",
                    extra_data={"delivery_id": delivery_id, "error": str(e)},
                    exc_info=True,
                )
                msg = "❌ שגיאה בעיבוד הבקשה. נסה שוב."
                success = False

            background_tasks.add_task(send_telegram_message, send_chat_id, msg)
            return {
                "ok": True,
                "delivery_action": action,
                "delivery_id": delivery_id,
                "success": success,
            }

    # Get or create user (מזהה לפי from_user.id כשאפשר)
    user, is_new_user = await get_or_create_user(db, telegram_user_id, name)

    # לוג זיהוי משתמש — observability למעקב אחר חיפוש/יצירה
    logger.info(
        "User resolved",
        extra_data={
            "resolved_user_id": user.id,
            "telegram_chat_id": telegram_user_id,
            "lookup_by": "telegram_chat_id",
            "is_new": is_new_user,
            "role": user.role.value if user.role else None,
        },
    )

    state_manager = StateManager(db)

    if is_new_user:
        background_tasks.add_task(send_welcome_message, send_chat_id)
        return {"ok": True, "new_user": True}

    # טיפול ב-/start בכל שלב: איפוס ההקשר וחזרה לנקודת כניסה בטוחה
    if update.message and text.strip().startswith("/start"):
        if user.role == UserRole.COURIER:
            await db.refresh(user)
            if user.approval_status != ApprovalStatus.APPROVED:
                # שליח לא מאושר - מחזירים ל-INITIAL לא ל-MENU
                await state_manager.force_state(
                    user.id, "telegram", CourierState.INITIAL.value, context={}
                )
                handler = CourierStateHandler(db)
                response, new_state = await handler.handle_message(user, "תפריט", None)
            else:
                response, new_state = await _route_to_role_menu(user, db, state_manager)
        else:
            response, new_state = await _route_to_role_menu(user, db, state_manager)

        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True, "new_state": new_state, "reset": True}

    # Handle "#" to return to main menu
    if text.strip() == "#":
        # רענון מהDB לפני בדיקת סטטוס - למניעת stale data אם האדמין אישר בינתיים
        await db.refresh(user)

        if user.role == UserRole.COURIER and user.approval_status != ApprovalStatus.APPROVED:
            # שליח לא מאושר - מחזירים אותו להיות שולח רגיל
            user.role = UserRole.SENDER
            await db.commit()
            from app.state_machine.states import SenderState

            await state_manager.force_state(
                user.id, "telegram", SenderState.MENU.value, context={}
            )
            background_tasks.add_task(send_welcome_message, send_chat_id)
            return {
                "ok": True,
                "new_state": SenderState.MENU.value,
                "switched_from_non_approved_courier": True,
            }

        response, new_state = await _route_to_role_menu(user, db, state_manager)
        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True, "new_state": new_state}

    # שליפת state פעם אחת לכל ההמשך
    current_state = await state_manager.get_current_state(user.id, "telegram")

    # "חזרה לתפריט" מתנהג כמו לחיצה על # (כולל איפוס state) — גם אם המשתמש הגיע עם state תקוע
    if "חזרה לתפריט" in text and user.role not in (UserRole.COURIER, UserRole.STATION_OWNER):
        response, new_state = await _route_to_role_menu(user, db, state_manager)
        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True, "new_state": new_state, "reset": True}

    # הגנה מפני state תקוע שלא תואם role (למשל role שונה חיצונית בזמן זרימה)
    # בלי זה, המשתמש יכול להיתקע בלולאה של הודעת welcome ללא reset אמיתי.
    if isinstance(current_state, str):
        if current_state.startswith("STATION.") and user.role != UserRole.STATION_OWNER:
            logger.warning(
                "Stale station-owner state for role-mismatched user; resetting to role menu",
                extra_data={"user_id": user.id, "role": str(user.role), "state": current_state},
            )
            response, new_state = await _route_to_role_menu(user, db, state_manager)
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state, "reset": True}

        if current_state.startswith("DISPATCHER.") and user.role != UserRole.COURIER:
            logger.warning(
                "Stale dispatcher state for role-mismatched user; resetting to role menu",
                extra_data={"user_id": user.id, "role": str(user.role), "state": current_state},
            )
            response, new_state = await _route_to_role_menu(user, db, state_manager)
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state, "reset": True}

        if current_state.startswith("COURIER.") and user.role != UserRole.COURIER:
            logger.warning(
                "Stale courier state for role-mismatched user; resetting to role menu",
                extra_data={"user_id": user.id, "role": str(user.role), "state": current_state},
            )
            response, new_state = await _route_to_role_menu(user, db, state_manager)
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state, "reset": True}

        if current_state.startswith("SENDER.") and user.role not in (UserRole.SENDER, UserRole.ADMIN):
            logger.warning(
                "Stale sender state for role-mismatched user; resetting to role menu",
                extra_data={"user_id": user.id, "role": str(user.role), "state": current_state},
            )
            response, new_state = await _route_to_role_menu(user, db, state_manager)
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state, "reset": True}

    is_in_multi_step_flow = _is_in_multi_step_flow(user, current_state)

    # כפתורי תפריט ראשי/שיווק - guard אחד
    if not is_in_multi_step_flow:
        if user.role == UserRole.SENDER:
            for keyword, handler_fn in _SENDER_BUTTON_ROUTES:
                if keyword in text:
                    response, new_state = await handler_fn(
                        user, db, state_manager, text, photo_file_id
                    )
                    _queue_response_send(background_tasks, send_chat_id, response)
                    payload: dict = {"ok": True}
                    if new_state:
                        payload["new_state"] = new_state
                    return payload

    # ==================== ניתוב לפי תפקיד (handler לכל role) ====================

    if user.role == UserRole.STATION_OWNER:
        station = await _get_station_for_owner_or_downgrade(user, db)
        if station is not None:
            handler = StationOwnerStateHandler(db, station.id)
            response, new_state = await handler.handle_message(user, text, photo_file_id)
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state}

        response, new_state = await _route_to_role_menu(user, db, state_manager)
        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True, "new_state": new_state}

    if user.role == UserRole.COURIER:
        is_dispatcher_menu_click = ("תפריט סדרן" in text) or ("🏪 תפריט סדרן" in text)
        is_dispatcher_flow = isinstance(current_state, str) and current_state.startswith("DISPATCHER.")

        if is_dispatcher_menu_click or is_dispatcher_flow:
            station = await _get_dispatcher_station(user, db)

            if station is not None:
                if is_dispatcher_menu_click:
                    await state_manager.force_state(
                        user.id, "telegram", DispatcherState.MENU.value, context={}
                    )
                    handler = DispatcherStateHandler(db, station.id)
                    response, new_state = await handler.handle_message(user, "תפריט", None)
                    _queue_response_send(background_tasks, send_chat_id, response)
                    return {"ok": True, "new_state": new_state}

                # זרימת סדרן פעילה
                if "חזרה לתפריט נהג" in text:
                    await state_manager.force_state(
                        user.id, "telegram", CourierState.MENU.value, context={}
                    )
                    handler = CourierStateHandler(db)
                    response, new_state = await handler.handle_message(user, "תפריט", None)
                    _queue_response_send(background_tasks, send_chat_id, response)
                    return {"ok": True, "new_state": new_state}

                handler = DispatcherStateHandler(db, station.id)
                response, new_state = await handler.handle_message(user, text, photo_file_id)
                _queue_response_send(background_tasks, send_chat_id, response)
                return {"ok": True, "new_state": new_state}

            # אין תחנה לסדרן - fallback לתפריט מתאים
            logger.warning(
                "Dispatcher station not found, falling back to role menu",
                extra_data={"user_id": user.id, "state": current_state},
            )
            response, new_state = await _route_to_role_menu(user, db, state_manager)
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state}

        # ניתוב רגיל של שליח
        previous_state = current_state
        handler = CourierStateHandler(db)
        response, new_state = await handler.handle_message(user, text, photo_file_id)

        # שליחת "כרטיס נהג" למנהלים רק במעבר הראשון למצב PENDING_APPROVAL
        if (
            new_state == CourierState.PENDING_APPROVAL.value
            and previous_state != CourierState.PENDING_APPROVAL.value
            and user.approval_status == ApprovalStatus.PENDING
        ):
            background_tasks.add_task(
                AdminNotificationService.notify_new_courier_registration,
                user.id,
                user.full_name or user.name or "לא צוין",
                user.service_area or "לא צוין",
                user.telegram_chat_id,
                user.id_document_url,
                "telegram",
                user.vehicle_category,
                user.selfie_file_id,
                user.vehicle_photo_file_id,
            )

        # צילום מסך להפקדה - הודעה למנהלים
        if photo_file_id:
            context = await state_manager.get_context(user.id, "telegram")
            if context.get("deposit_screenshot"):
                background_tasks.add_task(
                    AdminNotificationService.notify_deposit_request,
                    user.id,
                    user.full_name or user.name or "לא ידוע",
                    user.telegram_chat_id,
                    photo_file_id,
                )

        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True, "new_state": new_state}

    if user.role in (UserRole.SENDER, UserRole.ADMIN):
        # התחלת זרימת שולח רק עבור שולח/אדמין (guard תפקיד - מונע יירוט תפקידים אחרים)
        if "שלוח" in text or "חבילה" in text:
            handler = SenderStateHandler(db)
            response, new_state = await handler.handle_message(
                user_id=user.id, platform="telegram", message=text
            )
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state}

        # המשך זרימת שולח אם המשתמש באמצע זרימה
        if (
            current_state
            and not current_state.startswith("COURIER.")
            and not current_state.startswith("DISPATCHER.")
            and not current_state.startswith("STATION.")
            and current_state not in ["INITIAL", "SENDER.INITIAL"]
        ):
            handler = SenderStateHandler(db)
            response, new_state = await handler.handle_message(
                user_id=user.id, platform="telegram", message=text
            )
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state}

        background_tasks.add_task(send_welcome_message, send_chat_id)
        return {"ok": True}

    # תפקיד לא מוכר - אזהרה ו-fallback לשולח
    logger.warning(
        "Unknown user role in telegram webhook, falling back to sender",
        extra_data={"user_id": user.id, "role": str(user.role)},
    )
    try:
        response, new_state = await _sender_fallback(user, db, state_manager)
        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True, "new_state": new_state}
    except Exception as e:
        logger.error(
            "Failed sender fallback for unknown role",
            extra_data={"user_id": user.id, "role": str(user.role), "error": str(e)},
            exc_info=True,
        )
        background_tasks.add_task(send_welcome_message, send_chat_id)
        return {"ok": True}
