"""
Telegram Webhook Handler - Bot Gateway Layer
"""

import re
import hashlib
import secrets
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, TypeAlias
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.database import get_db
from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.state_machine.handlers import (
    SenderStateHandler,
    CourierStateHandler,
    MessageResponse,
)
from app.state_machine.states import (
    CourierState,
    DispatcherState,
    StationOwnerState,
    SenderState,
    DriverState,
)
from app.state_machine.dispatcher_handler import DispatcherStateHandler
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager
from app.domain.services.courier_approval_service import CourierApprovalService
from app.core.logging import get_logger
from app.core.circuit_breaker import get_telegram_circuit_breaker
from app.core.config import settings
from app.core.exceptions import TelegramError
from app.api.dependencies.webhook_auth import verify_telegram_webhook_token

logger = get_logger(__name__)

router = APIRouter()

# ממתין להערות דחייה — ממפה admin_chat_id → courier_id ב-Redis.
# כאשר מנהל לוחץ "❌ דחה", שומרים ב-Redis את ה-courier_id
# וממתינים להודעת הטקסט הבאה מהמנהל כהערת דחייה.
# רשומות פגות תוקף אוטומטית לפי REDIS_PENDING_REJECTION_TTL (ברירת מחדל: 5 דקות).
_PENDING_REJECTION_KEY_PREFIX = "shipmentbot:tg:pending_rejection:"

# מיפוי callback_data קצר → טקסט כפתור מלא (כדי לעמוד במגבלת 64 bytes של Telegram).
# נשמר ב-Redis עם TTL, ונפתר ב-webhook בעת לחיצה.
_INLINE_BUTTON_CALLBACK_PREFIX = "btn:"
_INLINE_BUTTON_KEY_PREFIX = "shipmentbot:tg:inline_btn:"
_INLINE_BUTTON_TTL_SECONDS = 2 * 24 * 60 * 60  # 48 שעות
_INLINE_BUTTON_UNAVAILABLE_CALLBACK = "btn:unavailable"

# ניקוי Reply Keyboard — מסמנים ב-Redis כדי לבצע פעם אחת לכל chat_id,
# ולא להעמיס 3 קריאות API בכל תפריט אחרי שהמקלדת הישנה נוקתה.
_REPLY_KEYBOARD_CLEARED_KEY_PREFIX = "shipmentbot:tg:reply_kb_cleared:"
_REPLY_KEYBOARD_CLEARED_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 יום


def _rejection_key(admin_chat_id: str) -> str:
    return f"{_PENDING_REJECTION_KEY_PREFIX}{admin_chat_id}"


def _inline_button_key(chat_id: str, callback_data: str) -> str:
    return f"{_INLINE_BUTTON_KEY_PREFIX}{chat_id}:{callback_data}"


def _reply_keyboard_cleared_key(chat_id: str) -> str:
    return f"{_REPLY_KEYBOARD_CLEARED_KEY_PREFIX}{chat_id}"


async def _was_reply_keyboard_cleared(chat_id: str) -> bool:
    """בדיקה best-effort אם כבר ניקינו Reply Keyboard בעבר."""
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        val = await r.get(_reply_keyboard_cleared_key(chat_id))
        return bool(val)
    except Exception:
        return False


async def _mark_reply_keyboard_cleared(chat_id: str) -> None:
    """סימון best-effort שה-Reply Keyboard כבר נוקה."""
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        await r.setex(
            _reply_keyboard_cleared_key(chat_id),
            _REPLY_KEYBOARD_CLEARED_TTL_SECONDS,
            "1",
        )
    except Exception:
        return


def _compact_callback_data_fallback(button_text: str) -> str | None:
    """Fallback ל-callback_data קצר שממשיך לנתב נכון גם בלי Redis.

    מחזיר מחרוזת קצרה שמכילה keyword שידוע שה-state machine מזהה,
    או None אם לא נמצא fallback בטוח.
    """
    text = (button_text or "").strip()
    if not text:
        return None

    # כפתורי תפריט שולח / שיווק
    if "הצטרפות למנוי" in text:
        return "הצטרפות למנוי"
    if "העלאת משלוח מהיר" in text:
        return "העלאת משלוח מהיר"
    if "פנייה לניהול" in text:
        return "פנייה לניהול"
    if "הצטרפות כתחנה" in text:
        return "הצטרפות כתחנה"

    # כפתורי ניווט נפוצים
    if "תפריט" in text:
        return "תפריט"
    if "חזרה" in text:
        return "חזרה"

    return None


async def _store_inline_button_mapping(
    chat_id: str,
    callback_data: str,
    button_text: str,
) -> bool:
    """שומר מיפוי callback_data→טקסט ב-Redis.

    משתמש ב-NX כדי למנוע דריסת ערך במקרה נדיר של התנגשות token.
    """
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        was_set = await r.set(
            _inline_button_key(chat_id, callback_data),
            button_text,
            ex=_INLINE_BUTTON_TTL_SECONDS,
            nx=True,
        )
        return bool(was_set)
    except Exception as e:
        logger.warning(
            "כשלון בשמירת מיפוי כפתור inline ב-Redis",
            extra_data={"chat_id": chat_id, "error": str(e)},
        )
        return False


async def _resolve_inline_button_mapping(
    chat_id: str, callback_data: str
) -> str | None:
    """פותח callback_data קצר לטקסט כפתור מלא, או None אם לא נמצא."""
    if not callback_data or not callback_data.startswith(
        _INLINE_BUTTON_CALLBACK_PREFIX
    ):
        return None
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        val = await r.get(_inline_button_key(chat_id, callback_data))
        return val if val else None
    except Exception as e:
        logger.warning(
            "כשלון בפתיחת מיפוי כפתור inline מ-Redis",
            extra_data={"chat_id": chat_id, "error": str(e)},
        )
        return None


async def _get_pending_rejection(
    admin_chat_id: str,
) -> tuple[str, int] | None:
    """מחזיר (target_type, target_id) אם יש דחייה ממתינה ב-Redis, אחרת None."""
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        val = await r.get(_rejection_key(admin_chat_id))
        if val is None:
            return None
        decoded = val if isinstance(val, str) else val.decode()
        if ":" in decoded:
            target_type, target_id_str = decoded.split(":", 1)
            return target_type, int(target_id_str)
        return "courier", int(decoded)
    except Exception as e:
        logger.error(
            "Redis get failed for pending rejection",
            extra_data={
                "admin_chat_id": admin_chat_id,
                "error": str(e),
            },
        )
        return None


async def _set_pending_rejection(
    admin_chat_id: str, target_id: int, target_type: str = "courier"
) -> bool:
    """שומר דחייה ממתינה ב-Redis עם TTL. מחזיר False אם Redis נכשל.

    Args:
        admin_chat_id: מזהה צ'אט המנהל
        target_id: מזהה המשתמש (שליח או נהג)
        target_type: סוג היעד — "courier" או "driver"
    """
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        await r.setex(
            _rejection_key(admin_chat_id),
            settings.REDIS_PENDING_REJECTION_TTL,
            f"{target_type}:{target_id}",
        )
        return True
    except Exception as e:
        logger.error(
            "Redis set failed for pending rejection",
            extra_data={
                "admin_chat_id": admin_chat_id,
                "target_id": target_id,
                "target_type": target_type,
                "error": str(e),
            },
        )
        return False


async def _pop_pending_rejection(
    admin_chat_id: str,
) -> tuple[str, int] | None:
    """מחזיר (target_type, target_id) ומוחק מ-Redis, או None.

    Returns:
        tuple של (סוג — "courier"/"driver", מזהה משתמש) או None
    """
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        val = await r.getdel(_rejection_key(admin_chat_id))
        if val is None:
            return None
        decoded = val if isinstance(val, str) else val.decode()
        # תמיכה בפורמט ישן (מספר בלבד) — ברירת מחדל courier
        if ":" in decoded:
            target_type, target_id_str = decoded.split(":", 1)
            return target_type, int(target_id_str)
        return "courier", int(decoded)
    except Exception as e:
        logger.error(
            "Redis pop failed for pending rejection",
            extra_data={
                "admin_chat_id": admin_chat_id,
                "error": str(e),
            },
        )
        return None


async def _clear_pending_rejection(admin_chat_id: str) -> None:
    """מוחק דחייה ממתינה מ-Redis (ללא החזרת ערך)."""
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        await r.delete(_rejection_key(admin_chat_id))
    except Exception as e:
        logger.error(
            "Redis delete failed for pending rejection",
            extra_data={
                "admin_chat_id": admin_chat_id,
                "error": str(e),
            },
        )


def _build_driver_rejection_context(
    user: User,
    profile: object,
) -> dict:
    """בונה קונטקסט רישום מפרופיל נהג קיים — כדי שכרטיס הנהג יוצג תקין אחרי דחייה."""
    from datetime import date as _date

    ctx: dict = {
        "reg_name": user.full_name or user.name or "לא צוין",
    }
    if hasattr(profile, "birth_date") and profile.birth_date:
        today = _date.today()
        bd = profile.birth_date
        age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        ctx["reg_age"] = str(age)
    if hasattr(profile, "vehicle_description") and profile.vehicle_description:
        ctx["reg_vehicle"] = profile.vehicle_description
    return ctx


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
            telegram_user_id = (
                str(message.from_user.id) if message.from_user else send_chat_id
            )
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

    if isinstance(current_state, str) and current_state.startswith(
        ("DISPATCHER.", "STATION.", "DRIVER.")
    ):
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

    await state_manager.force_state(
        user.id, "telegram", CourierState.INITIAL.value, context={}
    )
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
    return MessageResponse(station_text, keyboard=[["📞 פנייה לניהול"]])


async def _handle_sender_admin_contact() -> MessageResponse:
    """קישור WhatsApp ישיר למנהל הראשי (או fallback להודעה בתוך הבוט)."""
    if settings.ADMIN_WHATSAPP_NUMBER:
        admin_link = f"https://wa.me/{settings.ADMIN_WHATSAPP_NUMBER}"
        admin_text = "📞 <b>פנייה לניהול</b>\n\n" f"ליצירת קשר עם המנהל:\n{admin_link}"
    else:
        admin_text = (
            "📞 <b>פנייה לניהול</b>\n\n"
            "ליצירת קשר עם המנהל, שלחו הודעה כאן ונחזור אליכם בהקדם."
        )
    return MessageResponse(admin_text)


_sender_button_fast_shipment = _static_sender_button(_handle_sender_fast_shipment)
_sender_button_station_signup = _static_sender_button(_handle_sender_station_signup)


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
    # "פנייה לניהול" מטופל ב-handler גלובלי (לפני ניתוב לפי תפקיד) — פתוח לכל התפקידים
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
    db: AsyncSession, telegram_chat_id: str, name: Optional[str] = None
) -> tuple[User, bool]:
    """
    Get existing user or create new one. Returns (user, is_new).

    הגנה לפרודקשן:
    1. כפילויות היסטוריות — אם נמצאו מספר רשומות עם אותו telegram_chat_id,
       בוחרים את הפעילה/עדכנית ביותר ומשביתים את השאר.
    2. race condition — אם שתי בקשות מקבילות מנסות ליצור משתמש חדש
       באותו telegram_chat_id, IntegrityError נתפס ונעשית שאילתה מחדש.
    """
    result = await db.execute(
        select(User)
        .where(User.telegram_chat_id == telegram_chat_id)
        .order_by(
            User.is_active.desc().nulls_last(),
            User.updated_at.desc().nulls_last(),
            User.created_at.desc().nulls_last(),
        )
        .limit(10)
    )
    users = list(result.scalars().all())
    user = users[0] if users else None

    if len(users) > 1:
        # ניקוי כפילויות — השבתת רשומות כפולות כדי למנוע לוגים חוזרים
        duplicate_ids = [u.id for u in users[1:]]
        logger.warning(
            "כפילות telegram_chat_id — משבית רשומות כפולות",
            extra_data={
                "telegram_chat_id": telegram_chat_id,
                "kept_user_id": user.id,
                "deactivated_user_ids": duplicate_ids,
            },
        )
        for dup in users[1:]:
            dup.is_active = False
            dup.telegram_chat_id = None
        await db.commit()
        await db.refresh(user)

    if not user:
        user = User(
            # הערה: שומר placeholder כדי למנוע כשלי DB כש-phone_number מוגדר NOT NULL
            phone_number=_telegram_phone_placeholder(telegram_chat_id),
            telegram_chat_id=telegram_chat_id,
            name=name,
            platform="telegram",
            role=UserRole.SENDER,
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            # race condition — בקשה מקבילית יצרה את המשתמש לפנינו
            await db.rollback()
            logger.info(
                "IntegrityError ביצירת משתמש — שאילתה מחדש",
                extra_data={"telegram_chat_id": telegram_chat_id},
            )
            result = await db.execute(
                select(User)
                .where(User.telegram_chat_id == telegram_chat_id)
                .order_by(
                    User.is_active.desc().nulls_last(),
                    User.updated_at.desc().nulls_last(),
                )
                .limit(1)
            )
            user = result.scalars().first()
            if user is None:
                # מצב בלתי צפוי — IntegrityError אך אין רשומה תואמת
                logger.error(
                    "IntegrityError אך לא נמצא משתמש לאחר rollback",
                    extra_data={"telegram_chat_id": telegram_chat_id},
                )
                raise
            return user, False
        await db.refresh(user)
        return user, True  # New user

    return user, False  # Existing user


async def send_telegram_message(
    chat_id: str,
    text: str,
    keyboard: Optional[list] = None,
) -> None:
    """שליחת הודעה דרך Telegram Bot API — כפתורים תמיד inline, reply keyboard מנוקה אוטומטית"""
    import httpx
    from app.core.config import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram bot token not configured")
        return

    circuit_breaker = get_telegram_circuit_breaker()

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

    async def _build_inline_keyboard(button_rows: list) -> list[list[dict]]:
        """בניית inline keyboard עם callback_data קצר כשצריך (מגבלת 64 bytes)."""
        inline_keyboard: list[list[dict]] = []
        for row in button_rows:
            inline_row: list[dict] = []
            for button_text in row:
                text_str = str(button_text)
                callback_data = text_str
                if len(callback_data.encode("utf-8")) > 64:
                    # מגבלת Telegram: callback_data עד 64 bytes.
                    # מייצרים token קצר ושומרים ב-Redis עם NX למניעת התנגשות נדירה.
                    ok = False
                    for _attempt in range(3):
                        token = secrets.token_urlsafe(16)
                        candidate = f"{_INLINE_BUTTON_CALLBACK_PREFIX}{token}"
                        ok = await _store_inline_button_mapping(
                            chat_id=chat_id,
                            callback_data=candidate,
                            button_text=text_str,
                        )
                        if ok:
                            callback_data = candidate
                            break
                    if not ok:
                        # אם Redis לא זמין / התנגשות token: נעדיף fallback "חכם"
                        # שממשיך לעבוד (keyword קצר), ואם אין — נשתמש ב-btn:unavailable
                        # כדי שה-webhook יחזיר הודעת שגיאה ברורה.
                        compact = _compact_callback_data_fallback(text_str)
                        if compact and len(compact.encode("utf-8")) <= 64:
                            callback_data = compact
                        else:
                            callback_data = _INLINE_BUTTON_UNAVAILABLE_CALLBACK

                inline_row.append({"text": text_str, "callback_data": callback_data})
            inline_keyboard.append(inline_row)
        return inline_keyboard

    async def _send(payload: dict) -> dict:
        import json

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30.0)
            if response.status_code != 200:
                raise TelegramError.from_response(
                    "sendMessage",
                    response,
                    message=f"sendMessage returned status {response.status_code}",
                )
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                raise TelegramError(
                    "Telegram returned invalid JSON",
                    details={"status_code": response.status_code, "error": str(e)},
                ) from e
            if not data.get("ok"):
                raise TelegramError(
                    "sendMessage returned ok=false",
                    details={"response": data},
                )
            return data

    async def _delete_message(message_id: int) -> bool:
        """מחיקת הודעה — best-effort, לא זורק exception."""
        delete_url = (
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/deleteMessage"
        )
        delete_payload = {"chat_id": chat_id, "message_id": message_id}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    delete_url, json=delete_payload, timeout=30.0
                )
        except Exception as e:
            logger.debug(
                "כשלון בבקשת deleteMessage (best-effort)",
                extra_data={"chat_id": chat_id, "error": str(e)},
            )
            return False

        if response.status_code != 200:
            # best-effort: לא מפילים ולא פותחים circuit בגלל housekeeping
            logger.debug(
                "deleteMessage returned non-200 (best-effort)",
                extra_data={
                    "chat_id": chat_id,
                    "status_code": response.status_code,
                },
            )
            return False

        return True

    try:
        inline_keyboard: list[list[dict]] | None = None
        if keyboard:
            inline_keyboard = await _build_inline_keyboard(keyboard)

        # --- ניקוי reply keyboard ישן (פעם אחת לכל chat, רק כשיש כפתורים) ---
        if keyboard and not await _was_reply_keyboard_cleared(chat_id):
            placeholder_payload = {
                "chat_id": chat_id,
                "text": "\u200b",
                "parse_mode": "HTML",
                "reply_markup": {"remove_keyboard": True},
            }

            async def _send_placeholder() -> dict:
                return await _send(placeholder_payload)

            placeholder_data = await circuit_breaker.execute(_send_placeholder)
            placeholder_message_id = (placeholder_data.get("result") or {}).get(
                "message_id"
            )
            await _mark_reply_keyboard_cleared(chat_id)

            # best-effort: מחיקת ה-placeholder כדי לא להשאיר הודעה ריקה
            if placeholder_message_id:
                try:
                    await _delete_message(int(placeholder_message_id))
                except Exception:
                    pass

        # --- שליחה ---
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        if inline_keyboard:
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

        async def _send_final() -> dict:
            return await _send(payload)

        await circuit_breaker.execute(_send_final)
    except Exception as e:
        logger.error(
            "Telegram send failed",
            extra_data={"chat_id": chat_id, "error": str(e)},
            exc_info=True,
        )


async def answer_callback_query(callback_query_id: str, text: str = None) -> None:
    """Answer callback query to remove loading state with circuit breaker protection"""
    import httpx
    from app.core.config import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        return

    circuit_breaker = get_telegram_circuit_breaker()

    url = (
        f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    )
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
            exc_info=True,
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
    await send_telegram_message(
        chat_id,
        welcome_text,
        keyboard,
    )


async def _sender_fallback(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
) -> tuple[MessageResponse, str]:
    """fallback לתפריט שולח - משותף לכל ה-fallbacks ב-_route_to_role_menu"""
    await state_manager.force_state(
        user.id, "telegram", SenderState.MENU.value, context={}
    )
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
        await state_manager.force_state(
            user.id, "telegram", CourierState.MENU.value, context={}
        )
        handler = CourierStateHandler(db)
        return await handler.handle_message(user, "תפריט", None)

    if user.role == UserRole.STATION_OWNER:
        station = await _get_station_for_owner_or_downgrade(user, db)
        if station is not None:
            await state_manager.force_state(
                user.id, "telegram", StationOwnerState.MENU.value, context={}
            )
            handler = StationOwnerStateHandler(db, station.id)
            return await handler.handle_message(user, "תפריט", None)
        return await _sender_fallback(user, db, state_manager)

    if user.role == UserRole.DRIVER:
        # iDriver — ניתוב נהג ל-handler רישום (סשן 2)
        from app.state_machine.driver_handler import DriverStateHandler

        await state_manager.force_state(
            user.id, "telegram", DriverState.INITIAL.value, context={}
        )
        handler = DriverStateHandler(db, platform="telegram")
        return await handler.handle_message(user, "תפריט", None)

    if user.role == UserRole.SENDER or user.role == UserRole.ADMIN:
        # בדיקה אם המשתמש הוא סדרן פעיל — סדרנים שאינם שליחים נכנסים ישירות לתפריט סדרן
        dispatcher_station = await _get_dispatcher_station(user, db)
        if dispatcher_station is not None:
            await state_manager.force_state(
                user.id, "telegram", DispatcherState.MENU.value, context={}
            )
            handler = DispatcherStateHandler(db, dispatcher_station.id)
            return await handler.handle_message(user, "תפריט", None)

        # ADMIN מנוהל דרך ממשק אחר - בבוט מקבל תפריט שולח
        return await _sender_fallback(user, db, state_manager)

    # תפקיד לא מוכר - אזהרה בלוג ו-fallback לשולח
    logger.warning(
        "Unknown user role in menu routing, falling back to sender",
        extra_data={"user_id": user.id, "role": str(user.role)},
    )
    return await _sender_fallback(user, db, state_manager)


@router.post(
    "/webhook",
    summary="Webhook - Telegram (קבלת עדכונים נכנסים)",
    description=(
        "נקודת כניסה לקבלת עדכונים מ-Telegram Bot API. "
        "תומכת גם בהודעות טקסט/תמונות וגם ב-callback queries (כפתורים). "
        "מאומת באמצעות X-Telegram-Bot-Api-Secret-Token."
    ),
    responses={403: {"description": "טוקן אימות webhook חסר או שגוי"}},
)
async def telegram_webhook(
    update: TelegramUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_telegram_webhook_token),
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

    # פתיחת callback_data קצר (btn:*) לטקסט הכפתור המלא — כדי שה-state machine
    # ימשיך לעבוד על "טקסט" כמו בהודעות רגילות.
    if (
        event.is_callback
        and send_chat_id
        and text.startswith(_INLINE_BUTTON_CALLBACK_PREFIX)
    ):
        resolved = await _resolve_inline_button_mapping(send_chat_id, text)
        if resolved:
            text = resolved
        else:
            # הכפתור פג תוקף (TTL) או Redis לא זמין — לא מעבירים token ל-state machine.
            background_tasks.add_task(
                send_telegram_message,
                send_chat_id,
                (
                    "⚠️ הכפתור לא זמין כרגע. "
                    "אנא בקשו תפריט מחדש (/start או 'תפריט') ונסו שוב."
                    if text == _INLINE_BUTTON_UNAVAILABLE_CALLBACK
                    else "⏱️ הכפתור פג תוקף. אנא בקשו תפריט מחדש (/start או 'תפריט')."
                ),
            )
            return {"ok": True, "expired_inline_button": True}

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
            admin_ids = {
                cid.strip()
                for cid in settings.TELEGRAM_ADMIN_CHAT_IDS.split(",")
                if cid.strip()
            }
            if settings.TELEGRAM_ADMIN_CHAT_ID:
                admin_ids.add(settings.TELEGRAM_ADMIN_CHAT_ID)

            if clicker_id and clicker_id in admin_ids:
                action = courier_action.group(1)
                courier_id = int(courier_action.group(2))
                admin_name = name or "מנהל"

                if action == "approve":
                    # ניקוי דחייה ממתינה (אם המנהל לחץ דחה ואז אשר — מבטלים את הדחייה)
                    await _clear_pending_rejection(send_chat_id)

                    result = await CourierApprovalService.approve(db, courier_id)

                    # שליחת תוצאה למנהל (בצ'אט שבו לחץ)
                    background_tasks.add_task(
                        send_telegram_message, send_chat_id, result.message
                    )

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
                else:
                    # דחייה — אם יש כבר דחייה ממתינה (לחץ על דחייה פעמיים ברצף),
                    # מוחקים אטומית עם pop כדי שכשל ב-_set לא ישאיר ערך ישן
                    prev = await _pop_pending_rejection(send_chat_id)
                    if prev is not None:
                        _, prev_id = prev
                        background_tasks.add_task(
                            send_telegram_message,
                            send_chat_id,
                            f"⚠️ הדחייה הקודמת ({prev_id}) בוטלה. ממתין להערה על נהג {courier_id}.",
                        )

                    saved = await _set_pending_rejection(send_chat_id, courier_id, target_type="courier")
                    if not saved:
                        # Redis נכשל — דוחים ישירות ללא הערה כדי לא לאבד את הפעולה
                        result = await CourierApprovalService.reject(db, courier_id)
                        background_tasks.add_task(
                            send_telegram_message,
                            send_chat_id,
                            f"⚠️ שגיאת מערכת — הדחייה בוצעה ללא הערה.\n{result.message}",
                        )
                        if result.success and result.user:
                            from app.api.webhooks.whatsapp import send_whatsapp_message

                            admin_name = name or "מנהל"
                            background_tasks.add_task(
                                CourierApprovalService.notify_after_decision,
                                result.user,
                                "reject",
                                admin_name,
                                send_telegram_fn=send_telegram_message,
                                send_whatsapp_fn=send_whatsapp_message,
                            )
                        return {
                            "ok": True,
                            "admin_action": "reject_immediate_redis_fail",
                            "courier_id": courier_id,
                        }

                    background_tasks.add_task(
                        send_telegram_message,
                        send_chat_id,
                        f"📝 כתוב הערת דחייה לנהג {courier_id}"
                        " (או שלח <b>ללא</b> לדחייה ללא הערה):"
                        "\n⏱ יש לך 5 דקות לשלוח הערה.",
                    )

                return {
                    "ok": True,
                    "admin_action": (
                        "reject_pending_note" if action == "reject" else action
                    ),
                    "courier_id": courier_id,
                }

            logger.warning(
                "Non-admin clicked approval button",
                extra_data={"clicker_id": clicker_id, "chat_id": send_chat_id},
            )
            return {"ok": True}

    # טיפול בכפתורי אישור/דחיית נהג iDriver (מנהלים בלבד)
    if event.is_callback:
        driver_action = re.match(r"^(approve|reject)_driver_(\d+)$", text)
        if driver_action:
            clicker_id = telegram_user_id
            admin_ids = {
                cid.strip()
                for cid in settings.TELEGRAM_ADMIN_CHAT_IDS.split(",")
                if cid.strip()
            }
            if settings.TELEGRAM_ADMIN_CHAT_ID:
                admin_ids.add(settings.TELEGRAM_ADMIN_CHAT_ID)

            if clicker_id and clicker_id in admin_ids:
                action = driver_action.group(1)
                driver_user_id = int(driver_action.group(2))
                admin_name = name or "מנהל"

                from app.domain.services.driver_verification_service import (
                    DriverVerificationService,
                )

                if action == "approve":
                    # ניקוי דחייה ממתינה
                    await _clear_pending_rejection(send_chat_id)

                    result = await DriverVerificationService.approve_driver(
                        db, driver_user_id
                    )
                    background_tasks.add_task(
                        send_telegram_message, send_chat_id, result.message
                    )

                    if result.success and result.user and result.profile:
                        # עדכון מצב הנהג ל-MENU
                        state_manager = StateManager(db)
                        for plat in ("telegram", "whatsapp"):
                            await state_manager.force_state(
                                driver_user_id, plat, DriverState.MENU.value, context={}
                            )

                        # הודעה לנהג
                        tg_msg = (
                            "🎉 <b>האימות שלך אושר!</b>\n\n"
                            "ברוך הבא ל-iDriver!\n"
                            "כתוב <b>תפריט</b> כדי להתחיל."
                        )
                        if result.user.telegram_chat_id:
                            background_tasks.add_task(
                                send_telegram_message,
                                str(result.user.telegram_chat_id),
                                tg_msg,
                            )
                        elif (
                            result.user.phone_number
                            and not result.user.phone_number.startswith("tg:")
                            and not result.user.phone_number.endswith("@g.us")
                        ):
                            from app.api.webhooks.whatsapp import send_whatsapp_message

                            wa_msg = (
                                "🎉 *האימות שלך אושר!*\n\n"
                                "ברוך הבא ל-iDriver!\n"
                                "כתוב *תפריט* כדי להתחיל."
                            )
                            background_tasks.add_task(
                                send_whatsapp_message,
                                result.user.phone_number,
                                wa_msg,
                            )

                        # סיכום לקבוצת מנהלים
                        from app.domain.services.admin_notification_service import (
                            AdminNotificationService,
                        )

                        background_tasks.add_task(
                            AdminNotificationService.notify_group_driver_decision,
                            driver_user_id,
                            result.user.full_name or result.user.name or "לא צוין",
                            result.profile.dress_code or "לא צוין",
                            result.profile.vehicle_description or "לא צוין",
                            result.user.platform or "telegram",
                            "approved",
                            admin_name,
                        )
                else:
                    # דחייה — אותו תהליך כמו שליחים (Redis + הערה)
                    prev = await _pop_pending_rejection(send_chat_id)
                    if prev is not None:
                        _, prev_id = prev
                        background_tasks.add_task(
                            send_telegram_message,
                            send_chat_id,
                            f"⚠️ הדחייה הקודמת ({prev_id}) בוטלה. ממתין לסיבת דחייה על נהג {driver_user_id}.",
                        )

                    saved = await _set_pending_rejection(send_chat_id, driver_user_id, target_type="driver")
                    if not saved:
                        result = await DriverVerificationService.reject_driver(
                            db, driver_user_id
                        )
                        background_tasks.add_task(
                            send_telegram_message,
                            send_chat_id,
                            f"⚠️ שגיאת מערכת — הדחייה בוצעה ללא הערה.\n{result.message}",
                        )
                        if result.success and result.user and result.profile:
                            # עדכון מצב הנהג חזרה ל-VERIFY_COLLECT_SELFIE עם קונטקסט רישום
                            rej_ctx = _build_driver_rejection_context(
                                result.user, result.profile
                            )
                            state_manager = StateManager(db)
                            for plat in ("telegram", "whatsapp"):
                                await state_manager.force_state(
                                    driver_user_id,
                                    plat,
                                    DriverState.VERIFY_COLLECT_SELFIE.value,
                                    context=rej_ctx,
                                )

                            # הודעה לנהג
                            if result.user.telegram_chat_id:
                                background_tasks.add_task(
                                    send_telegram_message,
                                    str(result.user.telegram_chat_id),
                                    "😔 <b>האימות שלך נדחה.</b>\nתוכל לנסות שוב.",
                                )
                            elif (
                                result.user.phone_number
                                and not result.user.phone_number.startswith("tg:")
                                and not result.user.phone_number.endswith("@g.us")
                            ):
                                from app.api.webhooks.whatsapp import send_whatsapp_message

                                background_tasks.add_task(
                                    send_whatsapp_message,
                                    result.user.phone_number,
                                    "😔 *האימות שלך נדחה.*\nתוכל לנסות שוב.",
                                )

                        return {
                            "ok": True,
                            "admin_action": "reject_driver_immediate_redis_fail",
                            "driver_user_id": driver_user_id,
                        }

                    background_tasks.add_task(
                        send_telegram_message,
                        send_chat_id,
                        f"📝 כתוב סיבת דחייה לנהג {driver_user_id}"
                        " (או שלח <b>ללא</b> לדחייה ללא סיבה):"
                        "\n⏱ יש לך 5 דקות לשלוח סיבה.",
                    )

                return {
                    "ok": True,
                    "admin_action": (
                        "reject_driver_pending_note"
                        if action == "reject"
                        else f"approve_driver"
                    ),
                    "driver_user_id": driver_user_id,
                }

            logger.warning(
                "Non-admin clicked driver approval button",
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
                    send_telegram_message, send_chat_id, "❌ המשלוח לא נמצא."
                )
                return {"ok": True}

            # בדיקה שהסדרן שייך לתחנה של המשלוח הספציפי
            is_disp = await station_service.is_dispatcher_of_station(
                user.id, target_delivery.station_id
            )

            if not is_disp:
                background_tasks.add_task(
                    send_telegram_message,
                    send_chat_id,
                    "❌ אין לך הרשאה לאשר/לדחות משלוחים בתחנה זו.",
                )
                return {"ok": True}

            from app.domain.services.shipment_workflow_service import (
                ShipmentWorkflowService,
            )

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

    # טיפול בהערת דחייה ממתינה — מנהל שלחץ "❌ דחה" ושלח הערה
    pending_rejection = (
        (await _pop_pending_rejection(send_chat_id)) if not event.is_callback else None
    )
    if pending_rejection is not None:
        target_type, target_id = pending_rejection
        admin_name = name or "מנהל"
        stripped = text.strip()
        rejection_note = stripped if stripped and stripped != "ללא" else None

        if target_type == "driver":
            # דחיית נהג iDriver
            from app.domain.services.driver_verification_service import (
                DriverVerificationService,
            )

            result = await DriverVerificationService.reject_driver(
                db, target_id, rejection_reason=rejection_note
            )
            background_tasks.add_task(
                send_telegram_message, send_chat_id, result.message
            )

            if result.success and result.user and result.profile:
                # עדכון מצב הנהג חזרה ל-VERIFY_COLLECT_SELFIE עם קונטקסט רישום
                rej_ctx = _build_driver_rejection_context(
                    result.user, result.profile
                )
                state_manager_rej = StateManager(db)
                for plat in ("telegram", "whatsapp"):
                    await state_manager_rej.force_state(
                        target_id,
                        plat,
                        DriverState.VERIFY_COLLECT_SELFIE.value,
                        context=rej_ctx,
                    )

                # הודעה לנהג
                rej_reason_text = (
                    f"\nסיבה: {rejection_note}" if rejection_note else ""
                )
                if result.user.telegram_chat_id:
                    background_tasks.add_task(
                        send_telegram_message,
                        str(result.user.telegram_chat_id),
                        f"😔 <b>האימות שלך נדחה.</b>{rej_reason_text}\nתוכל לנסות שוב.",
                    )
                elif (
                    result.user.phone_number
                    and not result.user.phone_number.startswith("tg:")
                    and not result.user.phone_number.endswith("@g.us")
                ):
                    from app.api.webhooks.whatsapp import send_whatsapp_message

                    background_tasks.add_task(
                        send_whatsapp_message,
                        result.user.phone_number,
                        f"😔 *האימות שלך נדחה.*{rej_reason_text}\nתוכל לנסות שוב.",
                    )

                # סיכום לקבוצת מנהלים
                from app.domain.services.admin_notification_service import (
                    AdminNotificationService,
                )

                background_tasks.add_task(
                    AdminNotificationService.notify_group_driver_decision,
                    target_id,
                    result.user.full_name or result.user.name or "לא צוין",
                    result.profile.dress_code or "לא צוין",
                    result.profile.vehicle_description or "לא צוין",
                    result.user.platform or "telegram",
                    "rejected",
                    admin_name,
                    rejection_note,
                )

            return {
                "ok": True,
                "admin_action": "reject_driver",
                "driver_user_id": target_id,
            }
        else:
            # דחיית שליח (courier) — ברירת מחדל
            result = await CourierApprovalService.reject(
                db, target_id, rejection_note=rejection_note
            )
            background_tasks.add_task(
                send_telegram_message, send_chat_id, result.message
            )

            if result.success and result.user:
                from app.api.webhooks.whatsapp import send_whatsapp_message

                background_tasks.add_task(
                    CourierApprovalService.notify_after_decision,
                    result.user,
                    "reject",
                    admin_name,
                    send_telegram_fn=send_telegram_message,
                    send_whatsapp_fn=send_whatsapp_message,
                    rejection_note=rejection_note,
                )

            return {
                "ok": True,
                "admin_action": "reject",
                "courier_id": target_id,
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

        if (
            user.role == UserRole.COURIER
            and user.approval_status != ApprovalStatus.APPROVED
        ):
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
    if "חזרה לתפריט" in text and user.role not in (
        UserRole.COURIER,
        UserRole.STATION_OWNER,
    ):
        response, new_state = await _route_to_role_menu(user, db, state_manager)
        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True, "new_state": new_state, "reset": True}

    # הגנה מפני state תקוע שלא תואם role (למשל role שונה חיצונית בזמן זרימה)
    # בלי זה, המשתמש יכול להיתקע בלולאה של הודעת welcome ללא reset אמיתי.
    if isinstance(current_state, str):
        if current_state.startswith("STATION.") and user.role != UserRole.STATION_OWNER:
            logger.warning(
                "Stale station-owner state for role-mismatched user; resetting to role menu",
                extra_data={
                    "user_id": user.id,
                    "role": str(user.role),
                    "state": current_state,
                },
            )
            response, new_state = await _route_to_role_menu(user, db, state_manager)
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state, "reset": True}

        if current_state.startswith("DISPATCHER.") and user.role != UserRole.COURIER:
            # סדרנים שאינם שליחים — בודקים בטבלת station_dispatchers לפני איפוס
            dispatcher_station = await _get_dispatcher_station(user, db)
            if dispatcher_station is None:
                logger.warning(
                    "Stale dispatcher state for non-dispatcher user; resetting to role menu",
                    extra_data={
                        "user_id": user.id,
                        "role": str(user.role),
                        "state": current_state,
                    },
                )
                response, new_state = await _route_to_role_menu(user, db, state_manager)
                _queue_response_send(background_tasks, send_chat_id, response)
                return {"ok": True, "new_state": new_state, "reset": True}

        if current_state.startswith("COURIER.") and user.role != UserRole.COURIER:
            logger.warning(
                "Stale courier state for role-mismatched user; resetting to role menu",
                extra_data={
                    "user_id": user.id,
                    "role": str(user.role),
                    "state": current_state,
                },
            )
            response, new_state = await _route_to_role_menu(user, db, state_manager)
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state, "reset": True}

        if current_state.startswith("SENDER.") and user.role not in (
            UserRole.SENDER,
            UserRole.ADMIN,
        ):
            logger.warning(
                "Stale sender state for role-mismatched user; resetting to role menu",
                extra_data={
                    "user_id": user.id,
                    "role": str(user.role),
                    "state": current_state,
                },
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

    # פנייה לניהול — פתוח לכל התפקידים, ללא תלות ב-guard של זרימה רב-שלבית
    if "פנייה לניהול" in text:
        # שמירת flag בקונטקסט — ההודעה הבאה תועבר להנהלה
        await state_manager.update_context(
            user.id, "telegram", "contact_admin_pending", True
        )
        admin_text = (
            "📞 <b>פנייה לניהול</b>\n\n"
            "כתבו את ההודעה שלכם והיא תועבר להנהלה."
        )
        response = MessageResponse(admin_text, keyboard=[["🔙 חזרה לתפריט"]])
        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True}

    # העברת הודעה להנהלה — אם המשתמש לחץ "פנייה לניהול" בהודעה הקודמת
    _tg_context = await state_manager.get_context(user.id, "telegram")
    if _tg_context.get("contact_admin_pending"):
        await state_manager.update_context(
            user.id, "telegram", "contact_admin_pending", False
        )

        if "חזרה" in text or "תפריט" in text:
            response, new_state = await _route_to_role_menu(user, db, state_manager)
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state}

        user_name = user.full_name or user.name or "לא צוין"
        forward_text = (
            f"📨 פנייה מ-{user_name}\n"
            f"(Telegram: {send_chat_id})\n\n"
            f"{text}"
        )

        from app.domain.services.admin_notification_service import (
            AdminNotificationService,
            _parse_csv_setting,
        )

        sent = False
        if settings.TELEGRAM_ADMIN_CHAT_ID:
            sent = await AdminNotificationService._send_telegram_message(
                settings.TELEGRAM_ADMIN_CHAT_ID, forward_text
            )
        if not sent:
            tg_admins = _parse_csv_setting(
                settings.TELEGRAM_ADMIN_CHAT_IDS
            ) if settings.TELEGRAM_ADMIN_CHAT_IDS else []
            for admin_id in tg_admins:
                sent = await AdminNotificationService._send_telegram_message(
                    admin_id, forward_text
                ) or sent
        if not sent and settings.WHATSAPP_ADMIN_GROUP_ID:
            sent = await AdminNotificationService._send_whatsapp_admin_message(
                settings.WHATSAPP_ADMIN_GROUP_ID, forward_text
            )

        if sent:
            confirm_text = "✅ ההודעה נשלחה להנהלה. נחזור אליכם בהקדם!"
        else:
            confirm_text = (
                "⚠️ לא הצלחנו להעביר את ההודעה כרגע.\n"
                "אנא נסו שוב מאוחר יותר."
            )
            logger.error(
                "כשלון בהעברת פנייה להנהלה — אין יעד זמין",
                extra_data={"user_id": user.id},
            )

        confirm_response = MessageResponse(confirm_text)
        _queue_response_send(background_tasks, send_chat_id, confirm_response)
        return {"ok": True}

    # ==================== ניתוב לפי תפקיד (handler לכל role) ====================

    if user.role == UserRole.STATION_OWNER:
        station = await _get_station_for_owner_or_downgrade(user, db)
        if station is not None:
            handler = StationOwnerStateHandler(db, station.id)
            response, new_state = await handler.handle_message(
                user, text, photo_file_id
            )
            _queue_response_send(background_tasks, send_chat_id, response)
            return {"ok": True, "new_state": new_state}

        response, new_state = await _route_to_role_menu(user, db, state_manager)
        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True, "new_state": new_state}

    # ניתוב לתפריט סדרן / זרימת סדרן — פתוח לכל תפקיד שהוא סדרן פעיל [שלב 3.2]
    is_dispatcher_flow = isinstance(current_state, str) and current_state.startswith(
        "DISPATCHER."
    )
    # בדיקת keyword רק כשהמשתמש לא באמצע זרימת סדרן — מונע תפיסת טקסט חופשי כלחיצת כפתור
    is_dispatcher_menu_click = (not is_dispatcher_flow) and (
        ("תפריט סדרן" in text) or ("🏪 תפריט סדרן" in text)
    )

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

            # כפתור "חזרה לתפריט ראשי"/"חזרה לתפריט נהג" — חזרה לתפריט לפי תפקיד
            # חשוב: קוראים ישירות ל-fallback ולא ל-_route_to_role_menu כדי למנוע
            # לולאה (כי _route_to_role_menu יזהה שהמשתמש סדרן ויחזיר לתפריט סדרן)
            if "חזרה לתפריט נהג" in text or "חזרה לתפריט ראשי" in text:
                if user.role == UserRole.COURIER:
                    await state_manager.force_state(
                        user.id, "telegram", CourierState.MENU.value, context={}
                    )
                    handler = CourierStateHandler(db)
                    response, new_state = await handler.handle_message(
                        user, "תפריט", None
                    )
                else:
                    response, new_state = await _sender_fallback(
                        user, db, state_manager
                    )
                _queue_response_send(background_tasks, send_chat_id, response)
                return {"ok": True, "new_state": new_state}

            handler = DispatcherStateHandler(db, station.id)
            response, new_state = await handler.handle_message(
                user, text, photo_file_id
            )
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

    if user.role == UserRole.COURIER:
        # ניתוב רגיל של שליח
        previous_state = current_state
        handler = CourierStateHandler(db)
        response, new_state = await handler.handle_message(user, text, photo_file_id)

        # לוגיקה משותפת: כרטיס נהג + הפקדה (ייבוא inline למניעת circular import)
        from app.api.webhooks.whatsapp import _handle_courier_post_processing

        await _handle_courier_post_processing(
            db=db,
            user=user,
            previous_state=previous_state,
            new_state=new_state,
            contact_phone=user.telegram_chat_id,
            photo_file_id=photo_file_id,
            platform="telegram",
            background_tasks=background_tasks,
        )

        _queue_response_send(background_tasks, send_chat_id, response)
        return {"ok": True, "new_state": new_state}

    if user.role == UserRole.DRIVER:
        # iDriver — ניתוב נהג ל-handler רישום (סשן 2)
        from app.state_machine.driver_handler import DriverStateHandler

        is_driver_flow = isinstance(current_state, str) and current_state.startswith("DRIVER.")
        if not is_driver_flow:
            await state_manager.force_state(
                user.id, "telegram", DriverState.INITIAL.value, context={}
            )
        handler = DriverStateHandler(db, platform="telegram")
        response, new_state = await handler.handle_message(user, text, None)
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
            and not current_state.startswith("DRIVER.")
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
