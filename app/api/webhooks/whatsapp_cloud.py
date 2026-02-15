"""
WhatsApp Cloud API Webhook Handler — Arm A של המצב ההיברידי.

מקבל הודעות מ-Meta Cloud API (דרך pywa) ומנתב אותן
לאותם state machine handlers כמו ה-WPPConnect webhook.
תומך באימות חתימה, idempotency, ו-interactive buttons.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator
from app.db.database import get_db

# שימוש חוזר בפונקציות מ-webhook הקיים
from app.api.webhooks.whatsapp import (
    _try_acquire_message,
    _mark_message_completed,
    get_or_create_user,
    send_whatsapp_message,
    send_welcome_message,
    _route_to_role_menu_wa,
)
from app.state_machine.handlers import SenderStateHandler, CourierStateHandler
from app.state_machine.states import CourierState, DispatcherState
from app.state_machine.dispatcher_handler import DispatcherStateHandler
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager
from app.db.models.user import User, UserRole, ApprovalStatus

logger = get_logger(__name__)

router = APIRouter()


# ──────────────────────────────────────────────
#  אימות webhook — Meta verification & signature
# ──────────────────────────────────────────────


@router.get(
    "/webhook",
    summary="Cloud API Webhook Verification",
    description="אימות webhook מול Meta — מחזיר hub.challenge.",
    tags=["Webhooks"],
)
async def cloud_api_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
) -> int:
    """אימות webhook מול Meta — מחזיר hub.challenge אם verify_token מתאים."""
    if (
        hub_mode == "subscribe"
        and hub_verify_token
        and hub_verify_token == settings.WHATSAPP_CLOUD_API_VERIFY_TOKEN
    ):
        logger.info("Cloud API webhook verified successfully")
        return int(hub_challenge)
    logger.warning(
        "Cloud API webhook verification failed",
        extra_data={"hub_mode": hub_mode},
    )
    raise HTTPException(status_code=403, detail="Verification failed")


def _verify_signature(body: bytes, signature_header: str) -> bool:
    """אימות חתימת HMAC-SHA256 של Meta על ה-payload."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.WHATSAPP_CLOUD_API_APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature_header[7:], expected)


# ──────────────────────────────────────────────
#  חילוץ הודעות מפורמט Cloud API
# ──────────────────────────────────────────────


def _extract_text_from_message(msg: dict) -> str:
    """חילוץ טקסט מהודעת Cloud API — תמיכה בטקסט רגיל ו-interactive buttons."""
    msg_type = msg.get("type", "")

    if msg_type == "text":
        return msg.get("text", {}).get("body", "")

    # כפתור inline — callback data
    if msg_type == "interactive":
        interactive = msg.get("interactive", {})
        interactive_type = interactive.get("type", "")
        if interactive_type == "button_reply":
            # callback_data = ID הכפתור (הוגדר ב-PyWaProvider._build_buttons)
            return interactive.get("button_reply", {}).get("id", "")
        if interactive_type == "list_reply":
            return interactive.get("list_reply", {}).get("id", "")

    # כפתור template (quick reply)
    if msg_type == "button":
        return msg.get("button", {}).get("text", "")

    return ""


def _extract_media_from_message(msg: dict) -> tuple[str | None, str | None]:
    """חילוץ מדיה מהודעת Cloud API — מחזיר (media_id, media_type)."""
    msg_type = msg.get("type", "")

    if msg_type == "image":
        return msg.get("image", {}).get("id"), "image"
    if msg_type == "document":
        return msg.get("document", {}).get("id"), "document"
    if msg_type == "video":
        return msg.get("video", {}).get("id"), "video"

    return None, None


# ──────────────────────────────────────────────
#  Webhook handler ראשי
# ──────────────────────────────────────────────


@router.post(
    "/webhook",
    summary="Cloud API Webhook",
    description="קבלת הודעות מ-WhatsApp Cloud API (Meta).",
    responses={
        200: {"description": "הודעה התקבלה ועובדה"},
        403: {"description": "חתימה לא תקינה"},
    },
    tags=["Webhooks"],
)
async def cloud_api_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    קבלת ועיבוד הודעות מ-WhatsApp Cloud API.

    1. אימות חתימת Meta (X-Hub-Signature-256)
    2. חילוץ הודעות מפורמט Cloud API
    3. idempotency check
    4. זיהוי/יצירת משתמש (לפי מספר טלפון)
    5. ניתוב ל-state machine handlers
    """
    # אימות חתימה
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if settings.WHATSAPP_CLOUD_API_APP_SECRET and not _verify_signature(body, signature):
        logger.warning("Cloud API webhook: חתימה לא תקינה")
        raise HTTPException(status_code=403, detail="חתימה לא תקינה")

    payload = json.loads(body)
    responses: list[dict] = []

    # Cloud API payload: entry[] → changes[] → value.messages[]
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if value.get("messaging_product") != "whatsapp":
                continue

            for msg in value.get("messages", []):
                result = await _process_cloud_message(
                    db, msg, value, background_tasks
                )
                if result:
                    responses.append(result)

    return {"status": "ok", "responses": responses}


async def _process_cloud_message(
    db: AsyncSession,
    msg: dict,
    value: dict,
    background_tasks: BackgroundTasks,
) -> dict | None:
    """עיבוד הודעה בודדת מ-Cloud API.

    Cloud API מספק מספר טלפון נקי (ללא @c.us/@lid) — מפשט את זיהוי המשתמש.
    """
    message_id = msg.get("id", "")
    from_phone = msg.get("from", "")

    if not from_phone:
        return None

    phone_masked = PhoneNumberValidator.mask(from_phone)

    # חילוץ טקסט (כולל callback data מכפתורים)
    text = _extract_text_from_message(msg)
    media_id, media_type = _extract_media_from_message(msg)

    logger.debug(
        "Cloud API message received",
        extra_data={
            "from": phone_masked,
            "message_id": message_id,
            "type": msg.get("type", ""),
            "text_preview": text[:50] if text else "",
        },
    )

    # דילוג על הודעות ריקות
    if not text and not media_id:
        return None

    # idempotency — אותו מנגנון כמו WPPConnect webhook
    if not await _try_acquire_message(db, message_id, "whatsapp_cloud"):
        return None

    try:
        # Cloud API מספק מספר טלפון נקי — לא צריך את כל הלוגיקה של @lid/@c.us/wa:
        # נרמול: "972501234567" → "+972501234567"
        normalized_phone = f"+{from_phone}" if not from_phone.startswith("+") else from_phone

        user, is_new_user, _normalized = await get_or_create_user(
            db,
            sender_id=normalized_phone,
            from_number=normalized_phone,
            reply_to=normalized_phone,
            resolved_phone=normalized_phone,
        )

        logger.info(
            "Cloud API user resolved",
            extra_data={
                "user_id": user.id,
                "phone": phone_masked,
                "is_new": is_new_user,
                "role": user.role.value if user.role else None,
            },
        )

        # בדיקת טקסט מקדים מ-wa.me link (תפיסת משלוח)
        if text and text.startswith("capture_"):
            result = await _handle_capture_from_link(
                db, user, text, background_tasks, normalized_phone
            )
            if result:
                await _mark_message_completed(db, message_id)
                return result

        # משתמש חדש — הודעת ברוכים הבאים
        if is_new_user:
            background_tasks.add_task(send_whatsapp_message, normalized_phone, _welcome_text())
            await _mark_message_completed(db, message_id)
            return {"from": phone_masked, "response": "welcome", "new_user": True}

        # "#" — חזרה לתפריט ראשי
        if text.strip() in {"#", "תפריט ראשי", "menu"}:
            state_manager = StateManager(db)
            response, new_state = await _route_to_role_menu_wa(user, db, state_manager)
            background_tasks.add_task(
                send_whatsapp_message, normalized_phone, response.text, response.keyboard
            )
            await _mark_message_completed(db, message_id)
            return {"from": phone_masked, "response": response.text, "new_state": new_state}

        # ניתוב ל-state machine — אותה לוגיקה כמו WPPConnect webhook
        response, new_state = await _route_message_to_handler(
            db, user, text, media_id, background_tasks, normalized_phone
        )
        await _mark_message_completed(db, message_id)
        return {"from": phone_masked, "response": response, "new_state": new_state}

    except Exception as exc:
        logger.error(
            "Cloud API message processing failed",
            extra_data={
                "message_id": message_id,
                "phone": phone_masked,
                "error": str(exc),
            },
            exc_info=True,
        )
        await _mark_message_completed(db, message_id)
        return None


# ──────────────────────────────────────────────
#  תפיסת משלוח מקישור wa.me
# ──────────────────────────────────────────────


async def _handle_capture_from_link(
    db: AsyncSession,
    user: User,
    text: str,
    background_tasks: BackgroundTasks,
    reply_to: str,
) -> dict | None:
    """טיפול בטקסט מקדים capture_TOKEN — תפיסת משלוח מקישור wa.me."""
    token = text.split("_", 1)[1] if "_" in text else ""
    if not token:
        return None

    from app.domain.services.delivery_service import DeliveryService
    from app.db.models.delivery import Delivery, DeliveryStatus
    from sqlalchemy import select

    # חיפוש משלוח לפי token
    result = await db.execute(
        select(Delivery).where(Delivery.token == token)
    )
    delivery = result.scalar_one_or_none()

    if not delivery:
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            "❌ המשלוח לא נמצא. ייתכן שהקישור לא תקף יותר."
        )
        return {"from": PhoneNumberValidator.mask(reply_to), "response": "delivery_not_found"}

    if delivery.status not in (DeliveryStatus.OPEN, DeliveryStatus.PENDING_APPROVAL):
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            "❌ המשלוח כבר נתפס או אינו זמין יותר."
        )
        return {"from": PhoneNumberValidator.mask(reply_to), "response": "delivery_not_available"}

    # בדיקה שהמשתמש שליח מאושר
    if user.role != UserRole.COURIER or user.approval_status != ApprovalStatus.APPROVED:
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            "❌ רק שליחים מאושרים יכולים לתפוס משלוחים.\n"
            "הקלד # לחזרה לתפריט הראשי."
        )
        return {"from": PhoneNumberValidator.mask(reply_to), "response": "not_approved_courier"}

    # ניסיון תפיסה דרך DeliveryService
    delivery_service = DeliveryService(db)
    try:
        await delivery_service.capture_delivery(delivery.id, user.id)
        await db.commit()
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            f"✅ המשלוח נתפס בהצלחה!\n\n"
            f"📍 איסוף: {delivery.pickup_address}\n"
            f"🎯 יעד: {delivery.dropoff_address}\n"
            f"💰 עמלה: {delivery.fee}₪\n\n"
            f"הקלד # לחזרה לתפריט."
        )
        return {"from": PhoneNumberValidator.mask(reply_to), "response": "capture_success"}
    except Exception as exc:
        logger.error(
            "Cloud API capture failed",
            extra_data={
                "delivery_id": delivery.id,
                "user_id": user.id,
                "error": str(exc),
            },
            exc_info=True,
        )
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            f"❌ לא ניתן לתפוס את המשלוח: {str(exc)}"
        )
        return {"from": PhoneNumberValidator.mask(reply_to), "response": "capture_failed"}


# ──────────────────────────────────────────────
#  ניתוב הודעה ל-state machine handler
# ──────────────────────────────────────────────


async def _route_message_to_handler(
    db: AsyncSession,
    user: User,
    text: str,
    photo_file_id: str | None,
    background_tasks: BackgroundTasks,
    reply_to: str,
) -> tuple[str, str | None]:
    """ניתוב הודעה ל-handler המתאים לפי תפקיד המשתמש ומצב נוכחי."""
    state_manager = StateManager(db)
    current_state = await state_manager.get_current_state(user.id, "whatsapp")

    # בעל תחנה
    if user.role == UserRole.STATION_OWNER:
        from app.domain.services.station_service import StationService
        station_service = StationService(db)
        station = await station_service.get_station_by_owner(user.id)
        if station:
            handler = StationOwnerStateHandler(db, station.id, platform="whatsapp")
            response, new_state = await handler.handle_message(user, text, photo_file_id)
        else:
            response, new_state = await _route_to_role_menu_wa(user, db, state_manager)
        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard
        )
        return response.text, new_state

    # זרימת סדרן
    if current_state and isinstance(current_state, str) and current_state.startswith("DISPATCHER."):
        from app.domain.services.station_service import StationService
        station_service = StationService(db)
        station = await station_service.get_dispatcher_station(user.id)
        if station:
            handler = DispatcherStateHandler(db, station.id, platform="whatsapp")
            response, new_state = await handler.handle_message(user, text, photo_file_id)
        else:
            response, new_state = await _route_to_role_menu_wa(user, db, state_manager)
        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard
        )
        return response.text, new_state

    # שליח
    if user.role == UserRole.COURIER:
        handler = CourierStateHandler(db, platform="whatsapp")
        response, new_state = await handler.handle_message(user, text, photo_file_id)
        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard
        )
        return response.text, new_state

    # שולח (ברירת מחדל)
    handler = SenderStateHandler(db, platform="whatsapp")
    response, new_state = await handler.handle_message(user, text, photo_file_id)
    background_tasks.add_task(
        send_whatsapp_message, reply_to, response.text, response.keyboard
    )
    return response.text, new_state


def _welcome_text() -> str:
    """טקסט ברוכים הבאים — Cloud API (פרטי) עם כפתורים."""
    return (
        "👋 *ברוכים הבאים ל-ShipShare!*\n\n"
        "אנחנו מערכת שליחויות חכמה.\n"
        "מה תרצו לעשות?"
    )
