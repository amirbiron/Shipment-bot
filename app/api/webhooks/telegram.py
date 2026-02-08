"""
Telegram Webhook Handler - Bot Gateway Layer
"""
import re
import hashlib
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
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
from app.core.circuit_breaker import get_telegram_circuit_breaker
from app.core.config import settings
from app.core.exceptions import TelegramError

logger = get_logger(__name__)

router = APIRouter()

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
) -> tuple:
    """fallback לתפריט שולח - משותף לכל ה-fallbacks ב-_route_to_role_menu"""
    from app.state_machine.states import SenderState
    await state_manager.force_state(user.id, "telegram", SenderState.MENU.value, context={})
    handler = SenderStateHandler(db)
    return await handler.handle_message(
        user_id=user.id, platform="telegram", message="תפריט"
    )


async def _route_to_role_menu(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
) -> tuple:
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
        from app.domain.services.station_service import StationService
        station_service = StationService(db)
        station = await station_service.get_station_by_owner(user.id)

        if station:
            await state_manager.force_state(
                user.id, "telegram",
                StationOwnerState.MENU.value,
                context={}
            )
            handler = StationOwnerStateHandler(db, station.id)
            return await handler.handle_message(user, "תפריט", None)
        # בעל תחנה ללא תחנה פעילה - הורדת תפקיד לשולח כדי למנוע לולאה אינסופית
        # (אחרת כל הודעה תיתפס שוב ע"י הבלוק של STATION_OWNER בשורה 611)
        logger.warning(
            "Station owner without active station, downgrading to sender",
            extra_data={"user_id": user.id}
        )
        user.role = UserRole.SENDER
        await db.commit()
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
    # Handle callback queries (inline button clicks)
    if update.callback_query:
        callback = update.callback_query
        chat_id = _resolve_telegram_chat_id(update)
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

        # טיפול בכפתורי אישור/דחיית שליח (מנהלים בלבד)
        courier_action = re.match(r'^(approve|reject)_courier_(\d+)$', callback.data or "")
        if courier_action:
            # זיהוי מנהל לפי from_user.id (מי שלחץ) ולא לפי chat.id (איפה ההודעה)
            clicker_id = str(callback.from_user.id) if callback.from_user else None
            admin_ids = {cid.strip() for cid in settings.TELEGRAM_ADMIN_CHAT_IDS.split(",") if cid.strip()}
            if settings.TELEGRAM_ADMIN_CHAT_ID:
                admin_ids.add(settings.TELEGRAM_ADMIN_CHAT_ID)

            if clicker_id and clicker_id in admin_ids:
                action = courier_action.group(1)
                courier_id = int(courier_action.group(2))
                admin_name = name or "מנהל"

                # אישור או דחייה
                if action == "approve":
                    result = await CourierApprovalService.approve(db, courier_id)
                else:
                    result = await CourierApprovalService.reject(db, courier_id)

                # שליחת תוצאה למנהל (בצ'אט שבו לחץ)
                background_tasks.add_task(send_telegram_message, chat_id, result.message)

                # אם הפעולה הצליחה - הודעה לשליח וסיכום לקבוצה
                if result.success and result.user:
                    from app.api.webhooks.whatsapp import send_whatsapp_message
                    background_tasks.add_task(
                        CourierApprovalService.notify_after_decision,
                        result.user, action, admin_name,
                        send_telegram_fn=send_telegram_message,
                        send_whatsapp_fn=send_whatsapp_message,
                    )

                return {"ok": True, "admin_action": action, "courier_id": courier_id}

            # משתמש שאינו מנהל לחץ על כפתור אישור - מתעלמים
            logger.warning(
                "Non-admin clicked approval button",
                extra_data={"clicker_id": clicker_id, "chat_id": chat_id}
            )
            return {"ok": True}

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

    if not chat_id:
        logger.warning(
            "Telegram update missing chat_id; skipping processing",
            extra_data={
                "has_message": bool(update.message),
                "has_callback_query": bool(update.callback_query),
            },
        )
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
            if user.approval_status != ApprovalStatus.APPROVED:
                # שליח לא מאושר - מחזירים ל-INITIAL לא ל-MENU
                await state_manager.force_state(user.id, "telegram", CourierState.INITIAL.value, context={})
                handler = CourierStateHandler(db)
                response, new_state = await handler.handle_message(user, "תפריט", None)
            else:
                response, new_state = await _route_to_role_menu(user, db, state_manager)
        else:
            response, new_state = await _route_to_role_menu(user, db, state_manager)

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
        # רענון מהDB לפני בדיקת סטטוס - למניעת stale data אם האדמין אישר בינתיים
        await db.refresh(user)

        if user.role == UserRole.COURIER and user.approval_status != ApprovalStatus.APPROVED:
            # שליח לא מאושר - מחזירים אותו להיות שולח רגיל
            user.role = UserRole.SENDER
            await db.commit()
            from app.state_machine.states import SenderState
            await state_manager.force_state(user.id, "telegram", SenderState.MENU.value, context={})
            background_tasks.add_task(send_welcome_message, chat_id)
            return {"ok": True, "new_state": SenderState.MENU.value, "switched_from_non_approved_courier": True}

        response, new_state = await _route_to_role_menu(user, db, state_manager)

        background_tasks.add_task(
            send_telegram_message,
            chat_id,
            response.text,
            response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # טיפול בכפתורי תפריט ראשי [שלב 1]
    # הערה: הכפתורים הבאים פעילים רק למשתמשים שאינם באמצע תהליך רישום או זרימת סדרן/בעל תחנה.
    # שליח באמצע KYC, סדרן באמצע הוספת משלוח/חיוב, או בעל תחנה באמצע פעולה - ימשיכו ישירות ל-handler שלהם למטה.
    _current_state_value = await state_manager.get_current_state(user.id, "telegram")
    _is_courier_in_registration = (
        user.role == UserRole.COURIER
        and _current_state_value in {
            CourierState.REGISTER_COLLECT_NAME.value,
            CourierState.REGISTER_COLLECT_DOCUMENT.value,
            CourierState.REGISTER_COLLECT_SELFIE.value,
            CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value,
            CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value,
            CourierState.REGISTER_TERMS.value,
        }
    )
    _is_in_multi_step_flow = (
        _is_courier_in_registration
        or (isinstance(_current_state_value, str)
            and _current_state_value.startswith(("DISPATCHER.", "STATION.")))
    )

    if not _is_in_multi_step_flow:
        if user.role == UserRole.SENDER and ("הצטרפות למנוי" in text or "שליח" in text):
            # ניתוב לתהליך הרישום כנהג/שליח
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

        if ("העלאת משלוח מהיר" in text or "משלוח מהיר" in text) and user.role == UserRole.SENDER:
            # קישור חיצוני לקבוצת WhatsApp - משתמשים רגילים לא יכולים להעלות משלוח בתוך הבוט
            from app.core.config import settings as app_settings
            if app_settings.WHATSAPP_GROUP_LINK:
                msg_text = (
                    "📦 <b>העלאת משלוח מהיר</b>\n\n"
                    "להעלאת משלוח מהיר, הצטרפו לקבוצת WhatsApp שלנו:\n"
                    f"{app_settings.WHATSAPP_GROUP_LINK}"
                )
            else:
                msg_text = (
                    "📦 <b>העלאת משלוח מהיר</b>\n\n"
                    "להעלאת משלוח מהיר, פנו להנהלה לקבלת קישור לקבוצת WhatsApp."
                )
            from app.state_machine.handlers import MessageResponse as _MR
            resp = _MR(msg_text)
            background_tasks.add_task(
                send_telegram_message, chat_id, resp.text, resp.keyboard, False
            )
            return {"ok": True}

        if ("הצטרפות כתחנה" in text or "תחנה" in text) and user.role == UserRole.SENDER:
            # הודעה שיווקית עבור תחנות
            station_text = (
                "🏪 <b>הצטרפות כתחנה</b>\n\n"
                "המערכת של ShipShare מסדרת לך את התחנה!\n\n"
                "✅ ניהול נהגים אוטומטי\n"
                "✅ גבייה מסודרת\n"
                "✅ תיעוד משלוחים מלא\n"
                "✅ סדר בבלגן\n\n"
                "לפרטים נוספים, פנו להנהלה."
            )
            from app.state_machine.handlers import MessageResponse as _MR
            resp = _MR(station_text, keyboard=[["📞 פנייה לניהול"]], inline=True)
            background_tasks.add_task(
                send_telegram_message, chat_id, resp.text, resp.keyboard, resp.inline
            )
            return {"ok": True}

        if "פנייה לניהול" in text and user.role == UserRole.SENDER:
            # קישור WhatsApp ישיר למנהל הראשי
            from app.core.config import settings as app_settings
            if app_settings.ADMIN_WHATSAPP_NUMBER:
                admin_link = f"https://wa.me/{app_settings.ADMIN_WHATSAPP_NUMBER}"
                admin_text = (
                    "📞 <b>פנייה לניהול</b>\n\n"
                    f"ליצירת קשר עם המנהל:\n{admin_link}"
                )
            else:
                admin_text = (
                    "📞 <b>פנייה לניהול</b>\n\n"
                    "ליצירת קשר עם המנהל, שלחו הודעה כאן ונחזור אליכם בהקדם."
                )
            from app.state_machine.handlers import MessageResponse as _MR
            resp = _MR(admin_text)
            background_tasks.add_task(
                send_telegram_message, chat_id, resp.text, resp.keyboard, False
            )
            return {"ok": True}

        if ("חזרה לתפריט" in text
            and user.role not in (UserRole.COURIER, UserRole.STATION_OWNER)):
            # כפתור "חזרה לתפריט" - מנתב כמו לחיצה על #
            # שליחים רגילים חוזרים לתפריט הראשי.
            # שליחים מאושרים ייפלו ל-CourierStateHandler למטה.
            # בעלי תחנות ייפלו ל-StationOwnerStateHandler למטה.
            background_tasks.add_task(send_welcome_message, chat_id)
            return {"ok": True}

    # ==================== ניתוב לפי תפקיד [שלב 3] ====================

    # שימוש חוזר ב-_current_state_value שכבר חושב למעלה (שורה 502)
    current_state = _current_state_value

    # ניתוב לבעל תחנה [שלב 3.3]
    if user.role == UserRole.STATION_OWNER:
        from app.domain.services.station_service import StationService
        station_service = StationService(db)
        station = await station_service.get_station_by_owner(user.id)

        if station:
            handler = StationOwnerStateHandler(db, station.id)
            response, new_state = await handler.handle_message(user, text, photo_file_id)

            background_tasks.add_task(
                send_telegram_message,
                chat_id,
                response.text,
                response.keyboard,
                getattr(response, 'inline', False)
            )
            return {"ok": True, "new_state": new_state}

        # בעל תחנה ללא תחנה פעילה - fallback לתפריט שולח
        response, new_state = await _route_to_role_menu(user, db, state_manager)
        background_tasks.add_task(
            send_telegram_message, chat_id,
            response.text, response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # ניתוב לתפריט סדרן (כפתור "תפריט סדרן" בתפריט נהג) [שלב 3.2]
    if ("תפריט סדרן" in text or "🏪 תפריט סדרן" in text) and user.role == UserRole.COURIER:
        from app.domain.services.station_service import StationService
        station_service = StationService(db)
        station = await station_service.get_dispatcher_station(user.id)

        if station:
            await state_manager.force_state(
                user.id, "telegram",
                DispatcherState.MENU.value,
                context={}
            )
            handler = DispatcherStateHandler(db, station.id)
            response, new_state = await handler.handle_message(user, "תפריט", None)

            background_tasks.add_task(
                send_telegram_message,
                chat_id,
                response.text,
                response.keyboard,
                getattr(response, 'inline', False)
            )
            return {"ok": True, "new_state": new_state}

        # סדרן הוסר או תחנה בוטלה - חזרה לתפריט נהג ללא כפתור סדרן
        logger.warning(
            "Dispatcher clicked station menu but station not found",
            extra_data={"user_id": user.id}
        )
        response, new_state = await _route_to_role_menu(user, db, state_manager)
        background_tasks.add_task(
            send_telegram_message, chat_id,
            response.text, response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # אם המשתמש באמצע זרימת סדרן - ממשיכים עם DispatcherStateHandler
    if current_state and current_state.startswith("DISPATCHER."):
        from app.domain.services.station_service import StationService
        station_service = StationService(db)
        station = await station_service.get_dispatcher_station(user.id)

        if station:
            # כפתור "חזרה לתפריט נהג" מחזיר לתפריט הנהג הרגיל
            if "חזרה לתפריט נהג" in text:
                await state_manager.force_state(
                    user.id, "telegram",
                    CourierState.MENU.value,
                    context={}
                )
                handler = CourierStateHandler(db)
                response, new_state = await handler.handle_message(user, "תפריט", None)

                background_tasks.add_task(
                    send_telegram_message,
                    chat_id,
                    response.text,
                    response.keyboard,
                    getattr(response, 'inline', False)
                )
                return {"ok": True, "new_state": new_state}

            handler = DispatcherStateHandler(db, station.id)
            response, new_state = await handler.handle_message(user, text, photo_file_id)

            background_tasks.add_task(
                send_telegram_message,
                chat_id,
                response.text,
                response.keyboard,
                getattr(response, 'inline', False)
            )
            return {"ok": True, "new_state": new_state}

        # תחנה לא נמצאה (בוטלה או סדרן הוסר) - איפוס לתפריט נהג
        logger.warning(
            "Dispatcher station not found, resetting to courier menu",
            extra_data={"user_id": user.id, "state": current_state}
        )
        response, new_state = await _route_to_role_menu(user, db, state_manager)
        background_tasks.add_task(
            send_telegram_message, chat_id,
            response.text, response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # אם המשתמש באמצע זרימת בעל תחנה - ממשיכים
    if current_state and current_state.startswith("STATION."):
        from app.domain.services.station_service import StationService
        station_service = StationService(db)
        station = await station_service.get_station_by_owner(user.id)

        if station:
            handler = StationOwnerStateHandler(db, station.id)
            response, new_state = await handler.handle_message(user, text, photo_file_id)

            background_tasks.add_task(
                send_telegram_message,
                chat_id,
                response.text,
                response.keyboard,
                getattr(response, 'inline', False)
            )
            return {"ok": True, "new_state": new_state}

        # תחנה לא נמצאה (בוטלה?) - איפוס ל-fallback
        response, new_state = await _route_to_role_menu(user, db, state_manager)
        background_tasks.add_task(
            send_telegram_message, chat_id,
            response.text, response.keyboard,
            getattr(response, 'inline', False)
        )
        return {"ok": True, "new_state": new_state}

    # Route based on user role
    if user.role == UserRole.COURIER:
        # שמירת המצב הקודם לפני הטיפול בהודעה
        previous_state = await state_manager.get_current_state(user.id, "telegram")

        handler = CourierStateHandler(db)
        response, new_state = await handler.handle_message(user, text, photo_file_id)

        # שליחת "כרטיס נהג" למנהלים רק במעבר הראשון למצב PENDING_APPROVAL
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
                context.get("document_file_id"),
                "telegram",
                user.vehicle_category,
                user.selfie_file_id,
                user.vehicle_photo_file_id,
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

    # If user is in the middle of a sender flow, continue it
    # (current_state כבר חושב למעלה)
    if (current_state
        and not current_state.startswith("COURIER.")
        and not current_state.startswith("DISPATCHER.")
        and not current_state.startswith("STATION.")
        and current_state not in ["INITIAL", "SENDER.INITIAL"]):
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
