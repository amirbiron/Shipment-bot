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
    _reset_role_state_wa,
    _sender_fallback_wa,
    handle_admin_private_command,
    _is_whatsapp_admin_any,
    _match_delivery_approval_command,
    _handle_whatsapp_delivery_approval,
    _resolve_contact_phone,
    _handle_courier_post_processing,
)
from app.domain.services.capture_service import CaptureService
from app.domain.services.station_service import StationService
from app.state_machine.handlers import SenderStateHandler, CourierStateHandler
from app.state_machine.dispatcher_handler import DispatcherStateHandler
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager
from app.state_machine.states import CourierState, DispatcherState, DriverState
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
        and hub_challenge
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


def _extract_location_from_message(msg: dict) -> tuple[float | None, float | None]:
    """חילוץ מיקום GPS מהודעת Cloud API — מחזיר (latitude, longitude)."""
    msg_type = msg.get("type", "")
    if msg_type == "location":
        loc = msg.get("location", {})
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is not None and lng is not None:
            return float(lat), float(lng)
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
    # פיצ'ר 4: בדיקת חסימת IP אוטומטית לפני אימות חתימה
    from app.api.dependencies.webhook_signature import (
        _get_client_ip,
        _is_ip_blocked,
        _record_failed_attempt,
    )

    client_ip = _get_client_ip(request)
    if await _is_ip_blocked(client_ip):
        logger.warning(
            "Cloud API webhook: בקשה מ-IP חסום",
            extra_data={"client_ip": client_ip},
        )
        raise HTTPException(
            status_code=429,
            detail="IP חסום זמנית עקב ניסיונות אימות כושלים חוזרים",
        )

    # אימות חתימה
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    # אם סוד האפליקציה לא מוגדר — אי אפשר לאמת חתימות, דוחים את הבקשה
    if not settings.WHATSAPP_CLOUD_API_APP_SECRET:
        logger.error(
            "Cloud API webhook: WHATSAPP_CLOUD_API_APP_SECRET לא מוגדר — דוחה בקשה"
        )
        raise HTTPException(status_code=403, detail="חתימה לא ניתנת לאימות")

    if not _verify_signature(body, signature):
        await _record_failed_attempt(client_ip)
        logger.warning(
            "Cloud API webhook: חתימה לא תקינה",
            extra_data={"client_ip": client_ip},
        )
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
    location_lat, location_lng = _extract_location_from_message(msg)

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
    # מיקום GPS עובר את ה-guard — בדיקת תפקיד נהג מתבצעת אחרי זיהוי המשתמש
    _is_location_only = (not text and not media_id and location_lat is not None)
    if not text and not media_id and not _is_location_only:
        return None

    # idempotency — אותו מנגנון כמו WPPConnect webhook
    if not await _try_acquire_message(db, message_id, "whatsapp_cloud"):
        return None

    _msg_failed = False
    try:
        # Cloud API מספק מספר טלפון נקי — לא צריך את כל הלוגיקה של @lid/@c.us/wa:
        # נרמול: "972501234567" → "+972501234567"
        normalized_phone = f"+{from_phone}" if not from_phone.startswith("+") else from_phone

        user, is_new_user, _normalized = await get_or_create_user(
            db,
            sender_identifier=normalized_phone,
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

        # הודעת מיקום בלבד — רלוונטית רק לנהגים; שאר התפקידים לא צורכים מיקום GPS
        if _is_location_only and user.role != UserRole.DRIVER:
            return None

        # פקודות אישור/דחייה מהודעות פרטיות של מנהלים —
        # חייב להיות לפני is_new_user כדי שמנהל חדש יוכל לאשר מיד
        is_admin_sender = _is_whatsapp_admin_any(normalized_phone, user.phone_number)
        if is_admin_sender and text:
            admin_response = await handle_admin_private_command(
                db,
                text,
                admin_name=user.name or PhoneNumberValidator.mask(normalized_phone),
                background_tasks=background_tasks,
            )
            if admin_response:
                background_tasks.add_task(
                    send_whatsapp_message, normalized_phone, admin_response
                )
                return {"from": phone_masked, "response": admin_response, "admin_command": True}

        # משתמש חדש — הודעת ברוכים הבאים עם כפתורים.
        # חייב להיות לפני capture/delivery-approval כדי שמשתמש חדש
        # שלחץ על capture link לא יקבל רק שגיאה בלי onboarding.
        if is_new_user:
            background_tasks.add_task(send_welcome_message, normalized_phone)
            return {"from": phone_masked, "response": "welcome", "new_user": True}

        # בדיקת טקסט מקדים מ-wa.me link (תפיסת משלוח)
        if text and text.startswith("capture_"):
            result = await _handle_capture_from_link(
                db, user, text, background_tasks, normalized_phone
            )
            if result:
                return result

        # פקודות אישור/דחיית משלוח (סדרנים)
        if text:
            delivery_approval = _match_delivery_approval_command(text)
            if delivery_approval:
                action, delivery_id = delivery_approval
                station_service = StationService(db)
                from app.db.models.delivery import Delivery
                from sqlalchemy import select as sa_select

                delivery_result = await db.execute(
                    sa_select(Delivery).where(Delivery.id == delivery_id)
                )
                target_delivery = delivery_result.scalar_one_or_none()

                if not target_delivery or not target_delivery.station_id:
                    background_tasks.add_task(
                        send_whatsapp_message, normalized_phone,
                        "❌ המשלוח לא נמצא."
                    )
                    return {"from": phone_masked, "response": "delivery_not_found", "delivery_approval": True}

                is_disp = await station_service.is_dispatcher_of_station(
                    user.id, target_delivery.station_id
                )
                if not is_disp:
                    background_tasks.add_task(
                        send_whatsapp_message, normalized_phone,
                        "❌ אין לך הרשאה לאשר/לדחות משלוחים בתחנה זו."
                    )
                    return {"from": phone_masked, "response": "not_authorized", "delivery_approval": True}

                approval_msg = await _handle_whatsapp_delivery_approval(
                    db, action, delivery_id,
                    dispatcher_id=user.id,
                )
                background_tasks.add_task(
                    send_whatsapp_message, normalized_phone, approval_msg
                )
                return {"from": phone_masked, "response": approval_msg, "delivery_approval": True}

        # "#" — חזרה לתפריט ראשי
        if text and text.strip() in {"#", "תפריט ראשי", "menu"}:
            state_manager = StateManager(db)
            response, new_state = await _route_to_role_menu_wa(user, db, state_manager)
            background_tasks.add_task(
                send_whatsapp_message, normalized_phone, response.text, response.keyboard, response.button_text
            )
            return {"from": phone_masked, "response": response.text, "new_state": new_state}

        # ניתוב ל-state machine — אותה לוגיקה כמו WPPConnect webhook
        response, new_state = await _route_message_to_handler(
            db, user, text, media_id, background_tasks, normalized_phone,
            location_lat=location_lat, location_lng=location_lng,
        )
        return {"from": phone_masked, "response": response, "new_state": new_state}

    except Exception as exc:
        _msg_failed = True
        logger.error(
            "Cloud API message processing failed",
            extra_data={
                "message_id": message_id,
                "phone": phone_masked,
                "error": str(exc),
            },
            exc_info=True,
        )
        return None

    finally:
        # סימון הודעה כ-completed רק אם העיבוד הצליח —
        # הודעה שנכשלה נשארת ב-processing ומאפשרת retry אחרי timeout
        if not _msg_failed and message_id:
            try:
                await _mark_message_completed(db, message_id)
            except Exception:
                logger.error(
                    "Failed to mark message as completed",
                    extra_data={"message_id": message_id},
                    exc_info=True,
                )


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

    phone_masked = PhoneNumberValidator.mask(reply_to)

    # בדיקה שהמשתמש שליח מאושר
    if user.role != UserRole.COURIER or user.approval_status != ApprovalStatus.APPROVED:
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            "❌ רק שליחים מאושרים יכולים לתפוס משלוחים.\n"
            "הקלד # לחזרה לתפריט הראשי."
        )
        return {"from": phone_masked, "response": "not_approved_courier"}

    # ניסיון תפיסה דרך CaptureService — מטפל בחיפוש לפי token, ולידציית סטטוס, ונעילת ארנק
    capture_service = CaptureService(db)
    try:
        success, message, delivery = await capture_service.capture_delivery_by_token(token, user.id)
    except Exception as exc:
        logger.error(
            "Cloud API capture failed",
            extra_data={
                "token": token[:8] + "...",
                "user_id": user.id,
                "error": str(exc),
            },
            exc_info=True,
        )
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            "❌ אירעה שגיאה בתפיסת המשלוח. נסה שוב מאוחר יותר."
        )
        return {"from": phone_masked, "response": "capture_failed"}

    if not success:
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            f"❌ {message}"
        )
        return {"from": phone_masked, "response": "capture_rejected"}

    # הצלחה — הצגת פרטי המשלוח
    if delivery:
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            f"✅ {message}\n\n"
            f"📍 איסוף: {delivery.pickup_address}\n"
            f"🎯 יעד: {delivery.dropoff_address}\n"
            f"💰 עמלה: {delivery.fee}₪\n\n"
            f"הקלד # לחזרה לתפריט."
        )
    else:
        background_tasks.add_task(
            send_whatsapp_message, reply_to,
            f"✅ {message}\nהקלד # לחזרה לתפריט."
        )
    return {"from": phone_masked, "response": "capture_success"}


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
    *,
    location_lat: float | None = None,
    location_lng: float | None = None,
) -> tuple[str, str | None]:
    """ניתוב הודעה ל-handler המתאים לפי תפקיד המשתמש ומצב נוכחי."""
    state_manager = StateManager(db)
    current_state = await state_manager.get_current_state(user.id, "whatsapp")

    # guard: לא ליירט כפתורי תפריט בזמן זרימה רב-שלבית
    _is_courier_in_registration = (
        user.role == UserRole.COURIER
        and current_state in {
            CourierState.REGISTER_COLLECT_NAME.value,
            CourierState.REGISTER_COLLECT_DOCUMENT.value,
            CourierState.REGISTER_COLLECT_SELFIE.value,
            CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value,
            CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value,
            CourierState.REGISTER_TERMS.value,
        }
    )
    _is_in_multi_step_flow = _is_courier_in_registration or (
        isinstance(current_state, str)
        and (
            current_state.startswith(("DISPATCHER.", "STATION.", "DRIVER.", "ADMIN."))
            or (current_state.startswith("SENDER.") and current_state != "SENDER.MENU")
        )
    )

    # חזרה לאדמין — אדמין שהחליף תפקיד רוצה לחזור
    if text and "חזרה לאדמין" in text:
        _ctx = await state_manager.get_context(user.id, "whatsapp")
        if _ctx.get("original_role") == "admin":
            from app.state_machine.admin_handler import AdminStateHandler
            from app.state_machine.states import AdminState

            original_approval = _ctx.get("original_approval_status")
            user.role = UserRole.ADMIN
            if original_approval is not None:
                user.approval_status = ApprovalStatus(original_approval) if original_approval else None
            else:
                user.approval_status = None
            await db.commit()

            await state_manager.force_state(
                user.id, "whatsapp", AdminState.MENU.value,
                context={
                    "original_role": None,
                    "original_approval_status": None,
                    "admin_station_id": None,
                    "admin_target_role": None,
                },
            )
            _admin_h = AdminStateHandler(db, platform="whatsapp")
            response, new_state = await _admin_h.handle_message(user, "תפריט", None)
            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
            )
            return response.text, new_state

    # ניתוב אדמין — תפריט אדמין או המשך זרימת בחירת תפקיד
    if user.role == UserRole.ADMIN:
        from app.state_machine.admin_handler import AdminStateHandler
        from app.state_machine.states import AdminState

        if settings.ADMIN_ROLE_SWITCH_ENABLED:
            is_admin_flow = isinstance(current_state, str) and current_state.startswith("ADMIN.")
            if not is_admin_flow:
                await state_manager.force_state(
                    user.id, "whatsapp", AdminState.MENU.value, context={}
                )
            _admin_h = AdminStateHandler(db, platform="whatsapp")
            response, new_state = await _admin_h.handle_message(user, text, photo_file_id)

            # מצב מיוחד: admin_handler מחזיר _ADMIN_SWITCH_* כשצריך לנתב לתפקיד חדש
            if isinstance(new_state, str) and new_state.startswith("_ADMIN_SWITCH_"):
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
                )
                # _route_to_role_menu_wa שומר ומשחזר admin context אוטומטית,
                # ומוסיף כפתור "חזרה לאדמין" לתגובה
                response2, new_state2 = await _route_to_role_menu_wa(user, db, state_manager)
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response2.text, response2.keyboard, response2.button_text
                )
                return response2.text, new_state2

            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
            )
            return response.text, new_state

        # פיצ'ר כבוי — ניתוב לשולח
        if isinstance(current_state, str) and current_state.startswith("SENDER."):
            # אדמין כבר במצב שולח — המשך טיפול רגיל
            handler = SenderStateHandler(db)
            response, new_state = await handler.handle_message(
                user_id=user.id, platform="whatsapp", message=text
            )
        else:
            response, new_state = await _sender_fallback_wa(user, db, state_manager)
        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
        )
        return response.text, new_state

    # כפתורי תפריט ראשי — רק לשולחים שאינם באמצע זרימה
    if text and not _is_in_multi_step_flow and user.role == UserRole.SENDER:
        # הצטרפות כשליח
        if "הצטרפות למנוי" in text or "שליח" in text:
            user.role = UserRole.COURIER
            await db.commit()
            await state_manager.force_state(
                user.id, "whatsapp", CourierState.INITIAL.value, context={}
            )
            handler = CourierStateHandler(db, platform="whatsapp")
            response, new_state = await handler.handle_message(user, text, photo_file_id)
            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
            )
            return response.text, new_state

        # הצטרפות כנהג (iDriver)
        if "הצטרפות כנהג" in text or "נהג" in text:
            from app.state_machine.driver_handler import DriverStateHandler

            user.role = UserRole.DRIVER
            await db.commit()
            await state_manager.force_state(
                user.id, "whatsapp", DriverState.INITIAL.value, context={}
            )
            handler = DriverStateHandler(db, platform="whatsapp")
            response, new_state = await handler.handle_message(user, text, photo_file_id)
            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
            )
            return response.text, new_state

        # העלאת משלוח מהיר
        if "העלאת משלוח מהיר" in text or "משלוח מהיר" in text:
            if settings.WHATSAPP_GROUP_LINK:
                msg_text = (
                    "📦 העלאת משלוח מהיר\n\n"
                    "להעלאת משלוח מהיר, הצטרפו לקבוצת WhatsApp שלנו:\n"
                    f"{settings.WHATSAPP_GROUP_LINK}"
                )
            else:
                msg_text = (
                    "📦 העלאת משלוח מהיר\n\n"
                    "להעלאת משלוח מהיר, פנו להנהלה לקבלת קישור לקבוצת WhatsApp."
                )
            background_tasks.add_task(send_whatsapp_message, reply_to, msg_text)
            return msg_text, None

        # הצטרפות כתחנה
        if "הצטרפות כתחנה" in text or "תחנה" in text:
            station_text = (
                "🏪 הצטרפות כתחנה\n\n"
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
            return station_text, None


        # חזרה לתפריט
        if "חזרה לתפריט" in text:
            background_tasks.add_task(send_welcome_message, reply_to)
            return "welcome", None

    # פנייה לניהול — פתוח לכל התפקידים, ללא תלות ב-guard של זרימה רב-שלבית
    if text and "פנייה לניהול" in text:
        # שמירת flag בקונטקסט — ההודעה הבאה תועבר להנהלה
        await state_manager.update_context(
            user.id, "whatsapp", "contact_admin_pending", True
        )
        admin_text = (
            "📞 פנייה לניהול\n\n"
            "כתבו את ההודעה שלכם והיא תועבר להנהלה."
        )
        background_tasks.add_task(
            send_whatsapp_message, reply_to, admin_text, [["🔙 חזרה לתפריט"]]
        )
        return admin_text, None

    # העברת הודעה להנהלה — אם המשתמש לחץ "פנייה לניהול" בהודעה הקודמת
    _cloud_context = await state_manager.get_context(user.id, "whatsapp")
    if _cloud_context.get("contact_admin_pending"):
        await state_manager.update_context(
            user.id, "whatsapp", "contact_admin_pending", False
        )

        if text and ("חזרה" in text or "תפריט" in text):
            response, new_state = await _route_to_role_menu_wa(user, db, state_manager)
            background_tasks.add_task(
                send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
            )
            return response.text, new_state

        # plain text — ה-escape לטלגרם מתבצע ב-forward_support_message
        # מספר טלפון מלא — כדי שהאדמין יוכל ליצור קשר חזרה
        user_name = user.full_name or user.name or "לא צוין"
        forward_text = (
            f"📨 פנייה מ-{user_name}\n"
            f"({reply_to})\n\n"
            f"{text or '(הודעה ריקה)'}"
        )

        from app.domain.services.admin_notification_service import (
            AdminNotificationService,
        )

        sent = await AdminNotificationService.forward_support_message(
            forward_text, user.id, prefer_telegram=False
        )

        if sent:
            confirm_text = "✅ ההודעה נשלחה להנהלה. נחזור אליכם בהקדם!"
        else:
            confirm_text = (
                "⚠️ לא הצלחנו להעביר את ההודעה כרגע.\n"
                "אנא נסו שוב מאוחר יותר."
            )

        background_tasks.add_task(
            send_whatsapp_message, reply_to, confirm_text,
            [["🔙 חזרה לתפריט"]],
        )
        # חזרה לתפריט המתאים לתפקיד המשתמש — רק איפוס state
        _menu_state = await _reset_role_state_wa(user, db, state_manager)
        return confirm_text, _menu_state

    # בעל תחנה
    if user.role == UserRole.STATION_OWNER:
        station_service = StationService(db)
        station = await station_service.get_station_by_owner(user.id)
        if station:
            handler = StationOwnerStateHandler(db, station.id, platform="whatsapp")
            response, new_state = await handler.handle_message(user, text, photo_file_id)
            # הוספת כפתור "חזרה לאדמין" אם נדרש
            _station_ctx_cl = await state_manager.get_context(user.id, "whatsapp")
            if _station_ctx_cl.get("original_role") == "admin":
                from app.api.webhooks._admin_context import inject_admin_return_button as _inject_btn_station_cl
                _inject_btn_station_cl(response)
        else:
            response, new_state = await _route_to_role_menu_wa(user, db, state_manager)
        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
        )
        return response.text, new_state

    # זרימת סדרן
    if current_state and isinstance(current_state, str) and current_state.startswith("DISPATCHER."):
        station_service = StationService(db)
        station = await station_service.get_dispatcher_station(user.id)
        if station:
            # כפתור "חזרה לתפריט ראשי"/"חזרה לתפריט נהג" — חזרה לתפריט לפי תפקיד.
            # חשוב: קוראים ישירות ל-fallback ולא ל-_route_to_role_menu_wa כדי למנוע
            # לולאה (כי _route_to_role_menu_wa יזהה שהמשתמש סדרן ויחזיר לתפריט סדרן).
            if text and ("חזרה לתפריט נהג" in text or "חזרה לתפריט ראשי" in text):
                # אדמין שהחליף תפקיד — שחזור ישיר לתפריט אדמין
                # (לא _route_to_role_menu_wa — כי הוא יזהה סדרן ויחזור ללולאה)
                _back_ctx_cl = await state_manager.get_context(user.id, "whatsapp")
                if _back_ctx_cl.get("original_role") == "admin":
                    from app.api.webhooks._admin_context import restore_admin_role_and_route
                    response, new_state = await restore_admin_role_and_route(
                        user, db, state_manager, "whatsapp"
                    )
                elif user.role == UserRole.COURIER:
                    await state_manager.force_state(
                        user.id, "whatsapp", CourierState.MENU.value, context={}
                    )
                    handler = CourierStateHandler(db, platform="whatsapp")
                    response, new_state = await handler.handle_message(
                        user, "תפריט", None
                    )
                else:
                    response, new_state = await _sender_fallback_wa(
                        user, db, state_manager
                    )
            else:
                handler = DispatcherStateHandler(db, station.id, platform="whatsapp")
                response, new_state = await handler.handle_message(user, text, photo_file_id)
                # הוספת כפתור "חזרה לאדמין" אם נדרש
                _disp_ctx_cl = await state_manager.get_context(user.id, "whatsapp")
                if _disp_ctx_cl.get("original_role") == "admin":
                    from app.api.webhooks._admin_context import inject_admin_return_button
                    inject_admin_return_button(response)
        else:
            logger.warning(
                "Dispatcher station not found, resetting to menu",
                extra_data={"user_id": user.id, "state": current_state},
            )
            response, new_state = await _route_to_role_menu_wa(user, db, state_manager)
        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
        )
        return response.text, new_state

    # ניתוב לתפריט סדרן (כפתור "תפריט סדרן" — פתוח לכל מי שהוא סדרן פעיל)
    # בדיקת keyword רק כשהמשתמש לא באמצע זרימת סדרן — מונע תפיסת טקסט חופשי
    _in_dispatcher_flow = isinstance(current_state, str) and current_state.startswith("DISPATCHER.")
    if text and not _in_dispatcher_flow and ("תפריט סדרן" in text or "🏪 תפריט סדרן" in text):
        station_service = StationService(db)
        station = await station_service.get_dispatcher_station(user.id)

        if station:
            from app.api.webhooks._admin_context import (
                save_admin_context as _save_ac_cl,
                restore_admin_context as _restore_ac_cl,
                inject_admin_return_button as _inject_btn_cl,
            )
            _dm_ak_cl = await _save_ac_cl(user.id, state_manager, "whatsapp")
            await state_manager.force_state(
                user.id, "whatsapp", DispatcherState.MENU.value, context={}
            )
            handler = DispatcherStateHandler(db, station.id, platform="whatsapp")
            response, new_state = await handler.handle_message(user, "תפריט", None)
            # שחזור admin context אחרי ה-handler
            if _dm_ak_cl:
                await _restore_ac_cl(
                    user.id, state_manager, new_state, _dm_ak_cl, "whatsapp"
                )
            if _dm_ak_cl and _dm_ak_cl.get("original_role") == "admin":
                _inject_btn_cl(response)
        else:
            logger.warning(
                "Dispatcher clicked station menu but station not found",
                extra_data={"user_id": user.id},
            )
            response, new_state = await _route_to_role_menu_wa(user, db, state_manager)

        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
        )
        return response.text, new_state

    # שליח
    if user.role == UserRole.COURIER:
        # שמירת המצב הקודם לפני הטיפול בהודעה
        previous_state = current_state

        handler = CourierStateHandler(db, platform="whatsapp")
        response, new_state = await handler.handle_message(user, text, photo_file_id)

        # לוגיקה משותפת: כרטיס נהג + הפקדה
        # ב-Cloud API מספר הטלפון נקי — reply_to הוא המספר עצמו
        contact_phone = _resolve_contact_phone(
            resolved_phone=None,
            from_number=reply_to,
            reply_to=reply_to,
            sender_id=reply_to,
            stored_phone=user.phone_number,
        )
        await _handle_courier_post_processing(
            db=db,
            user=user,
            previous_state=previous_state,
            new_state=new_state,
            contact_phone=contact_phone,
            photo_file_id=photo_file_id,
            platform="whatsapp",
            background_tasks=background_tasks,
        )

        # הוספת כפתור "חזרה לאדמין" אם נדרש
        _courier_ctx_cl = await state_manager.get_context(user.id, "whatsapp")
        if _courier_ctx_cl.get("original_role") == "admin":
            from app.api.webhooks._admin_context import inject_admin_return_button as _inject_btn_courier_cl
            _inject_btn_courier_cl(response)
        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
        )
        return response.text, new_state

    # נהג (iDriver)
    if user.role == UserRole.DRIVER:
        from app.state_machine.driver_handler import DriverStateHandler as _DH
        from app.domain.services.driver_session_service import DriverSessionService as _DSS

        # סשן 6: עדכון פעילות אחרונה בכל הודעה מנהג
        _session_svc = _DSS(db)
        await _session_svc.touch_session(user.id)

        is_driver_flow = isinstance(current_state, str) and current_state.startswith("DRIVER.")
        from app.api.webhooks._admin_context import (
            save_admin_context as _save_ac_drv_cl,
            restore_admin_context as _restore_ac_drv_cl,
            inject_admin_return_button as _inject_btn_drv_cl,
        )
        _drv_admin_keys_cl = None
        if not is_driver_flow:
            _drv_admin_keys_cl = await _save_ac_drv_cl(
                user.id, state_manager, "whatsapp"
            )
            await state_manager.force_state(
                user.id, "whatsapp", DriverState.INITIAL.value, context={}
            )
        _driver_handler = _DH(db, platform="whatsapp")
        response, new_state = await _driver_handler.handle_message(
            user, text, photo_file_id,
            location_lat=location_lat, location_lng=location_lng,
        )
        # שחזור admin context אחרי ה-handler
        if _drv_admin_keys_cl:
            await _restore_ac_drv_cl(
                user.id, state_manager, new_state,
                _drv_admin_keys_cl, "whatsapp",
            )
        # הוספת כפתור "חזרה לאדמין" אם נדרש
        if _drv_admin_keys_cl and _drv_admin_keys_cl.get("original_role") == "admin":
            _inject_btn_drv_cl(response)
        else:
            _driver_ctx_cl = await state_manager.get_context(user.id, "whatsapp")
            if _driver_ctx_cl.get("original_role") == "admin":
                _inject_btn_drv_cl(response)
        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
        )
        return response.text, new_state

    # שולח — רק אם באמצע זרימת שולח פעילה.
    # משתמש ב-INITIAL / SENDER.INITIAL / ללא state → welcome message (כמו ב-WPPConnect).
    if (
        current_state
        and current_state not in ("INITIAL", "SENDER.INITIAL")
        and not current_state.startswith("COURIER.")
        and not current_state.startswith("DISPATCHER.")
        and not current_state.startswith("STATION.")
        and not current_state.startswith("DRIVER.")
        and not current_state.startswith("ADMIN.")
    ):
        handler = SenderStateHandler(db)
        response, new_state = await handler.handle_message(
            user_id=user.id, platform="whatsapp", message=text
        )
        # הוספת כפתור "חזרה לאדמין" אם נדרש
        _sender_ctx_cl = await state_manager.get_context(user.id, "whatsapp")
        if _sender_ctx_cl.get("original_role") == "admin":
            from app.api.webhooks._admin_context import inject_admin_return_button as _inject_btn_sender_cl
            _inject_btn_sender_cl(response)
        background_tasks.add_task(
            send_whatsapp_message, reply_to, response.text, response.keyboard, response.button_text
        )
        return response.text, new_state

    # ברירת מחדל: welcome message עם בחירת תפקיד
    # לוג אם התפקיד אינו אחד מהמוכרים — עוזר לזהות תפקידים חדשים שלא טופלו
    if user.role not in (
        UserRole.ADMIN, UserRole.STATION_OWNER, UserRole.COURIER,
        UserRole.DRIVER, UserRole.SENDER,
    ):
        logger.warning(
            "תפקיד לא מזוהה בניתוב WhatsApp Cloud — חוזר ל-welcome",
            extra_data={"user_id": user.id, "role": str(user.role)},
        )
    background_tasks.add_task(send_welcome_message, reply_to)
    return "welcome", None


