"""
WhatsApp Webhook Handler - Bot Gateway Layer
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, BackgroundTasks, Request
from pydantic import BaseModel, model_validator
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, update

from app.api.dependencies.webhook_signature import require_wppconnect_signature
from app.db.database import get_db
from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.webhook_event import WebhookEvent
from app.state_machine.handlers import SenderStateHandler, CourierStateHandler
from app.state_machine.states import CourierState, DispatcherState, SenderState, StationOwnerState, DriverState
from app.state_machine.dispatcher_handler import DispatcherStateHandler
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager
from app.domain.services import AdminNotificationService
from app.domain.services.courier_approval_service import CourierApprovalService
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator
from app.core.config import settings
from app.domain.services.whatsapp import get_whatsapp_provider, get_whatsapp_group_provider

logger = get_logger(__name__)

# ──────────────────────────────────────────────
#  מנגנון idempotency – מבוסס DB, לא memory cache.
#  מסמן הודעה כ-processing רק אחרי INSERT מוצלח.
#  אם העיבוד נכשל — הרשומה נשארת processing ומאפשרת retry אחרי timeout.
#  רק אחרי עיבוד מלא הסטטוס הופך ל-completed — שחוסם כפילויות.
# ──────────────────────────────────────────────
_STALE_PROCESSING_SECONDS = 120  # הודעה ב-processing יותר מ-2 דקות = תקועה, מאפשרים retry


async def _try_acquire_message(db: AsyncSession, message_id: str, platform: str) -> bool:
    """
    ניסיון לרכוש הודעה לעיבוד (idempotency check).
    מחזיר True אם ההודעה חדשה ואפשר לעבד, False אם כפולה.
    גישה אופטימיסטית: INSERT קודם, טיפול בקיים אחר כך.
    """
    if not message_id:
        return True  # הודעה ללא ID — מאפשרים עיבוד (אין מה לדדפ)

    # ניסיון אופטימיסטי — הוספת הודעה חדשה ב-savepoint
    try:
        async with db.begin_nested():
            db.add(WebhookEvent(
                message_id=message_id,
                platform=platform,
                status="processing",
                created_at=datetime.now(timezone.utc),
            ))
        # commit מיידי כדי שהרשומה תישמר גם אם העיבוד נכשל —
        # מונע retry מיידי ומכריח המתנה של _STALE_PROCESSING_SECONDS
        await db.commit()
        return True
    except IntegrityError:
        pass  # הודעה כבר קיימת — בדיקה אם completed או stale

    # הודעה קיימת — בדיקה אם כבר הושלמה
    result = await db.execute(
        select(WebhookEvent.status, WebhookEvent.created_at)
        .where(WebhookEvent.message_id == message_id)
    )
    row = result.one_or_none()
    if not row:
        return False

    if row.status == "completed":
        logger.info(
            "Skipping completed duplicate message",
            extra_data={"message_id": message_id},
        )
        return False

    # ניסיון retry אטומי — UPDATE רק אם ההודעה תקועה מעבר ל-threshold
    threshold = datetime.now(timezone.utc) - timedelta(seconds=_STALE_PROCESSING_SECONDS)
    update_result = await db.execute(
        update(WebhookEvent)
        .where(
            WebhookEvent.message_id == message_id,
            WebhookEvent.status == "processing",
            WebhookEvent.created_at < threshold,
        )
        .values(created_at=datetime.now(timezone.utc))
    )

    if update_result.rowcount > 0:
        # commit מיידי — אותה סיבה כמו ב-INSERT
        await db.commit()
        logger.warning(
            "Retrying stale processing message",
            extra_data={"message_id": message_id},
        )
        return True

    logger.info(
        "Skipping in-progress message",
        extra_data={"message_id": message_id},
    )
    return False


async def _mark_message_completed(db: AsyncSession, message_id: str) -> None:
    """סימון הודעה כ-completed אחרי עיבוד מוצלח + commit."""
    if not message_id:
        return
    await db.execute(
        update(WebhookEvent)
        .where(WebhookEvent.message_id == message_id)
        .values(status="completed")
    )
    await db.commit()

router = APIRouter()


class WhatsAppMessage(BaseModel):
    """Incoming WhatsApp message structure"""

    from_number: str
    # מזהה יציב לשיחה/שולח (למשל message.from של WPPConnect). אם לא נשלח, ניפול ל-from_number.
    sender_id: Optional[str] = None
    # יעד תשובה בפועל (יכול להיות phone@c.us או @lid). אם לא נשלח, ניפול ל-from_number.
    reply_to: Optional[str] = None
    # מספר טלפון אמיתי שהגטוויי הצליח לחלץ מ-LID (למשל מ-formattedName או contact info).
    # אופציונלי — אם קיים, משמש לזיהוי אדמין גם כשכל שאר המזהים הם LID.
    resolved_phone: Optional[str] = None
    message_id: str
    text: str = ""
    timestamp: int
    # Support for media messages
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    # סוג MIME של המדיה (למשל image/jpeg) - לזיהוי מסמכים שהם בעצם תמונות
    mime_type: Optional[str] = None
    # מיקום GPS — לחיפוש נסיעות לפי מיקום (iDriver סשן 5)
    # נתמך גם כשדות שטוחים וגם כאובייקט מקונן {"location": {"latitude": ..., "longitude": ...}}
    location_latitude: Optional[float] = None
    location_longitude: Optional[float] = None
    # אובייקט מיקום מקונן — נתמך עבור גטוויי שמעביר location כאובייקט
    location: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def _extract_nested_location(cls, values: dict) -> dict:  # type: ignore[override]
        """חילוץ lat/lng מאובייקט location מקונן אם השדות השטוחים ריקים"""
        if not isinstance(values, dict):
            return values
        loc = values.get("location")
        if isinstance(loc, dict):
            if values.get("location_latitude") is None and "latitude" in loc:
                values["location_latitude"] = loc["latitude"]
            if values.get("location_longitude") is None and "longitude" in loc:
                values["location_longitude"] = loc["longitude"]
        return values


class WhatsAppWebhookPayload(BaseModel):
    """WhatsApp webhook payload"""

    messages: list[WhatsAppMessage] = []


async def _get_verified_payload(
    request: Request,
    _signature: None = Depends(require_wppconnect_signature),
) -> WhatsAppWebhookPayload:
    """פרסור ה-payload רק אחרי אימות חתימה.

    מבטיח שבקשות לא מאומתות נדחות עם 403 לפני
    שפרטי ה-schema נחשפים דרך שגיאת ולידציה 422.

    Raises:
        RequestValidationError: אם ה-JSON לא תואם לסכמה (422)
    """
    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError

    body = await request.body()
    try:
        return WhatsAppWebhookPayload.model_validate_json(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _extract_real_phone(value: str | None) -> str | None:
    """
    ניסיון לחלץ מספר טלפון אמיתי מהשדות של WhatsApp.

    תומך בערכים כמו:
    - 0501234567
    - 972501234567
    - +972501234567
    - 972501234567@c.us / @lid
    """
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if "@" in raw:
        raw = raw.split("@")[0].strip()
    cleaned = re.sub(r"[^\d+]", "", raw)
    if not cleaned:
        return None
    if not PhoneNumberValidator.validate(cleaned):
        return None
    return PhoneNumberValidator.normalize(cleaned)


def _resolve_contact_phone(
    resolved_phone: str | None,
    from_number: str | None,
    reply_to: str | None,
    sender_id: str | None,
    stored_phone: str | None,
) -> str:
    """בחירת טלפון אמיתי להצגה למנהלים (עם fallback בטוח)."""
    for candidate in (resolved_phone, from_number, reply_to, sender_id, stored_phone):
        normalized = _extract_real_phone(candidate)
        if normalized:
            return normalized

    for fallback in (reply_to, from_number, sender_id, stored_phone):
        if fallback:
            return fallback

    return "לא ידוע"


async def get_or_create_user(
    db: AsyncSession,
    sender_identifier: str,
    from_number: str | None = None,
    reply_to: str | None = None,
    resolved_phone: str | None = None,
) -> tuple[User, bool, str | None]:
    """
    Get existing user or create new one. Returns (user, is_new, normalized_phone)

    בווטסאפ לא תמיד יש מספר טלפון יציב (למשל @lid), לכן אנחנו משתמשים במזהה שולח יציב
    בתור ה-"phone_number" במודל לצורך זיהוי ושמירת session.
    normalized_phone מוחזר כדי למנוע חישוב כפול בקוד הקורא.
    """
    import hashlib

    def _whatsapp_sender_placeholder(raw: str) -> str:
        """
        יצירת placeholder קצר ויציב ל-phone_number עבור מזהים ארוכים.

        הערה: עמודת phone_number מוגדרת VARCHAR(20). אם המזהה ארוך — PostgreSQL יזרוק שגיאה.
        """
        raw = (raw or "").strip()
        if not raw:
            return "wa:unknown"
        if len(raw) <= 20:
            return raw
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:17]
        return f"wa:{digest}"

    # ניסיון לחלץ מספר אמיתי (אם הגטוויי הצליח) כדי למנוע "משתמש כפול"
    # בין יצירת תחנה (מבוסס +972...) לבין שיחות WhatsApp (מבוסס sender_id/@lid).
    normalized_phone = (
        _extract_real_phone(resolved_phone)
        or _extract_real_phone(from_number)
        or _extract_real_phone(reply_to)
    )

    # חיפוש לפי מזהה שיחה יציב: משתמשים באותו placeholder גם ב-lookup וגם ביצירה
    # כדי למנוע מצב שבו sender_id ארוך נשמר כ-wa:<hash> אבל lookup מחפש את הערך הגולמי.
    sender_key_raw = (sender_identifier or "").strip()
    sender_key = _whatsapp_sender_placeholder(sender_key_raw)

    user_by_sender = None
    if sender_key:
        keys = [sender_key]
        if sender_key_raw and sender_key_raw != sender_key:
            # תמיכה לאחור/SQLite: אם איכשהו נשמר ערך גולמי ארוך (ב-SQLite אין הגבלת אורך),
            # נחפש גם אותו וגם את ה-hash. ייתכן ששניהם קיימים בפועל — אסור לקרוס.
            keys.append(sender_key_raw)

        result = await db.execute(
            select(User)
            .where(User.phone_number.in_(keys))
            # אם יש גם hash וגם raw — נעדיף hash (המצב התקין בקוד החדש),
            # ואז user פעיל ומעודכן יותר.
            .order_by(
                (User.phone_number == sender_key).desc(),
                User.is_active.desc().nulls_last(),
                User.updated_at.desc().nulls_last(),
                User.created_at.desc().nulls_last(),
            )
            .limit(2)
        )
        matches = list(result.scalars().all())
        user_by_sender = matches[0] if matches else None

        if len(matches) > 1:
            logger.error(
                "Multiple user records matched WhatsApp sender key; using first match",
                extra_data={
                    "sender_key": PhoneNumberValidator.mask(sender_key),
                    "matched_user_ids": [u.id for u in matches],
                },
            )

    user_by_phone = None
    if normalized_phone:
        result = await db.execute(
            select(User)
            .where(User.phone_number == normalized_phone)
            .order_by(
                User.is_active.desc().nulls_last(),
                User.updated_at.desc().nulls_last(),
                User.created_at.desc().nulls_last(),
            )
            .limit(2)
        )
        phone_matches = list(result.scalars().all())
        user_by_phone = phone_matches[0] if phone_matches else None

        if len(phone_matches) > 1:
            # למרות ש-phone_number מסומן כ-unique במודל, בפרודקשן ייתכנו נתונים היסטוריים לא עקביים.
            # אסור להפיל webhook — בוחרים דטרמיניסטית וממשיכים.
            logger.error(
                "Multiple user records matched normalized phone; using first match",
                extra_data={
                    "phone": PhoneNumberValidator.mask(normalized_phone),
                    "matched_user_ids": [u.id for u in phone_matches],
                },
            )

    # בחירת משתמש:
    # - אם יש משתמש לפי מספר אמיתי והוא בעל תחנה/שליח (תפקיד "חזק") — נעדיף אותו.
    # - אחרת נעדיף את המשתמש לפי sender_id כדי לשמר session יציב גם כש-reply_to משתנה (@lid/@c.us).
    if user_by_phone and user_by_phone.id != getattr(user_by_sender, "id", None):
        if user_by_phone.role in {UserRole.STATION_OWNER, UserRole.COURIER, UserRole.ADMIN}:
            return user_by_phone, False, normalized_phone

    if user_by_sender:
        # ריפוי: אם למשתמש יש placeholder (wa:...) ועכשיו יש לנו מספר אמיתי —
        # מעדכנים את phone_number כדי שחיפושים עתידיים לפי מספר אמיתי ימצאו אותו
        # ויימנעו מיצירת רשומה כפולה.
        if (
            normalized_phone
            and user_by_sender.phone_number
            and user_by_sender.phone_number.startswith("wa:")
            and not user_by_phone  # אין משתמש אחר עם המספר הזה
        ):
            try:
                async with db.begin_nested():
                    user_by_sender.phone_number = normalized_phone
                await db.commit()
                await db.refresh(user_by_sender)
                logger.info(
                    "עדכון phone_number מ-placeholder למספר אמיתי",
                    extra_data={
                        "user_id": user_by_sender.id,
                        "phone": PhoneNumberValidator.mask(normalized_phone),
                    },
                )
            except IntegrityError:
                # משתמש אחר כבר מחזיק את המספר הזה — ממשיכים עם ה-placeholder.
                # אין צורך ב-db.rollback() — ה-savepoint כבר בוטל אוטומטית.
                # rollback מלא היה מבטל את כל הטרנזקציה ומסיים expired objects.
                await db.refresh(user_by_sender)
                logger.warning(
                    "לא ניתן לעדכן phone_number — כבר קיים אצל משתמש אחר",
                    extra_data={
                        "user_id": user_by_sender.id,
                        "phone": PhoneNumberValidator.mask(normalized_phone),
                    },
                )
        return user_by_sender, False, normalized_phone

    if user_by_phone:
        return user_by_phone, False, normalized_phone

    # יצירת משתמש חדש — מעדיפים מספר אמיתי (אם קיים) על פני placeholder
    # כדי שחיפוש עתידי לפי normalized_phone ימצא את המשתמש ולא ייצור כפילות.
    if normalized_phone:
        create_identifier = normalized_phone
    else:
        create_identifier = (sender_identifier or reply_to or from_number or "").strip()
        create_identifier = _whatsapp_sender_placeholder(create_identifier)

    try:
        async with db.begin_nested():
            user = User(phone_number=create_identifier, platform="whatsapp", role=UserRole.SENDER)
            db.add(user)
        await db.commit()
        await db.refresh(user)
        return user, True, normalized_phone
    except IntegrityError:
        # race condition — משתמש אחר נוצר במקביל עם אותו phone_number.
        # אין צורך ב-db.rollback() — ה-savepoint כבר בוטל אוטומטית.
        # מבצעים חיפוש מחדש כדי להחזיר את המשתמש הקיים.
        logger.info(
            "IntegrityError ביצירת משתמש — כנראה נוצר במקביל, מנסה למצוא",
            extra_data={"phone": PhoneNumberValidator.mask(create_identifier)},
        )
        result = await db.execute(
            select(User).where(User.phone_number == create_identifier)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing, False, normalized_phone
        # לא אמור לקרות — IntegrityError בלי רשומה תואמת.
        # זורקים שגיאה ברורה כדי שה-webhook ידלג על ההודעה עם לוג מתאים.
        raise ValueError(
            f"לא ניתן ליצור או למצוא משתמש עם phone_number={PhoneNumberValidator.mask(create_identifier)}"
        )


def _is_group_target(identifier: str) -> bool:
    """בדיקה אם היעד הוא קבוצה (WPPConnect) או צ'אט פרטי (Cloud API)."""
    return identifier.endswith("@g.us")


async def send_whatsapp_message(
    phone_number: str, text: str, keyboard: list = None
) -> None:
    """
    שליחת הודעה דרך ספק WhatsApp הפעיל — ניתוב אוטומטי לפי סוג היעד.
    קבוצה (@g.us) → WPPConnect, פרטי → Cloud API (במצב hybrid) / WPPConnect (רגיל).
    ממיר תגי HTML לפורמט הספק לפני שליחה.
    fire-and-forget: שגיאות נרשמות בלוג ולא נזרקות חזרה.
    """
    if _is_group_target(phone_number):
        provider = get_whatsapp_group_provider()
    else:
        provider = get_whatsapp_provider()
    formatted_text = provider.format_text(text)
    try:
        await provider.send_text(to=phone_number, text=formatted_text, keyboard=keyboard)
    except Exception as exc:
        logger.error(
            "כשלון בשליחת הודעת WhatsApp",
            extra_data={
                "phone": PhoneNumberValidator.mask(phone_number),
                "error": str(exc),
            },
            exc_info=True,
        )


def _normalize_whatsapp_identifier(value: str) -> str:
    """נרמול מזהה וואטסאפ (מספר/מזהה) להשוואה עקבית"""
    if not value:
        return ""
    base = value.strip()
    if "@" in base:
        base = base.split("@")[0]
    digits = re.sub(r"\D", "", base)
    if not digits:
        return ""
    if digits.startswith("0"):
        digits = "972" + digits[1:]
    return digits


def _get_whatsapp_admin_numbers() -> set[str]:
    """מחזיר סט מספרי מנהלים פרטיים לוואטסאפ (מנורמלים)"""
    normalized = set()
    for raw in settings.WHATSAPP_ADMIN_NUMBERS.split(","):
        raw = raw.strip()
        if not raw:
            continue
        normalized_value = _normalize_whatsapp_identifier(raw)
        if normalized_value:
            normalized.add(normalized_value)
    return normalized


def _is_whatsapp_admin_any(*identifiers: str) -> bool:
    """
    בדיקה אם אחד המזהים שייך למנהל.
    תומך בכמה מזהים במקביל (sender_id / reply_to / from_number).
    """
    wa_admin_numbers = _get_whatsapp_admin_numbers()
    if not wa_admin_numbers:
        return False

    for identifier in identifiers:
        normalized = _normalize_whatsapp_identifier(identifier)
        if normalized and normalized in wa_admin_numbers:
            return True

    return False


def _is_whatsapp_admin(sender_id: str) -> bool:
    """
    בדיקה אם השולח הוא מנהל - תומך בנרמול:
    - @lid / @c.us
    - 050... לעומת 972...
    - +972 מול 972
    """
    return _is_whatsapp_admin_any(sender_id)


def _resolve_admin_send_target(
    sender_id: str,
    reply_to: str,
    from_number: str | None = None,
    *extra_identifiers: str,
) -> str:
    """
    מציאת כתובת שליחה למנהל — מעדיף את המספר מההגדרות (שאנחנו יודעים שעובד).

    כרטיס הנהג נשלח למנהל דרך המספר שבהגדרות (WHATSAPP_ADMIN_NUMBERS) ומגיע בהצלחה.
    אבל כש-reply_to הוא @lid, הגטוויי עשוי לא להצליח לשלוח אליו.
    לכן אם אנחנו מזהים התאמה לפי sender_id / reply_to / from_number / resolved_phone
    — נשלח למספר ההגדרות.

    אם הערך בהגדרות חסר סיומת (@c.us / @lid), נעדיף מזהה מקורי שכולל סיומת
    — כי הגטוויי צריך את הסיומת הנכונה כדי לשלוח.
    """
    # מיפוי: ספרות מנורמלות → מזהה מקורי (עם סיומת)
    # reply_to ראשון כי הגטוויי שלח אותו — first-wins guard נותן לו עדיפות
    all_identifiers = [reply_to, sender_id]
    if from_number:
        all_identifiers.append(from_number)
    for ident in extra_identifiers:
        if ident:
            all_identifiers.append(ident)

    normalized_candidates: set[str] = set()
    normalized_to_suffixed: dict[str, str] = {}
    for ident in all_identifiers:
        norm = _normalize_whatsapp_identifier(ident)
        if norm:
            normalized_candidates.add(norm)
            if "@" in ident and norm not in normalized_to_suffixed:
                normalized_to_suffixed[norm] = ident.strip()

    if not normalized_candidates:
        return reply_to

    for raw in settings.WHATSAPP_ADMIN_NUMBERS.split(","):
        raw = raw.strip()
        if not raw:
            continue
        norm_raw = _normalize_whatsapp_identifier(raw)
        if norm_raw in normalized_candidates:
            # אם הערך בהגדרות כולל סיומת — משתמשים בו כמות שהוא
            if "@" in raw:
                logger.debug(
                    "שליחה למנהל לפי מספר מההגדרות (עם סיומת)",
                    extra_data={
                        "original_reply_to": PhoneNumberValidator.mask(reply_to),
                        "resolved_to": PhoneNumberValidator.mask(raw),
                    }
                )
                return raw
            # אם הערך בהגדרות חסר סיומת — נעדיף מזהה מקורי שכולל סיומת
            suffixed = normalized_to_suffixed.get(norm_raw)
            if suffixed:
                logger.debug(
                    "הערך בהגדרות חסר סיומת, משתמשים במזהה מקורי עם סיומת",
                    extra_data={
                        "settings_value": PhoneNumberValidator.mask(raw),
                        "using_identifier": PhoneNumberValidator.mask(suffixed),
                    }
                )
                return suffixed
            # אין מזהה עם סיומת — משתמשים בערך מההגדרות (fallback)
            logger.debug(
                "שליחה למנהל לפי מספר מההגדרות (ללא סיומת)",
                extra_data={
                    "original_reply_to": PhoneNumberValidator.mask(reply_to),
                    "resolved_to": PhoneNumberValidator.mask(raw),
                }
            )
            return raw

    return reply_to


def _match_delivery_approval_command(text: str) -> tuple[str, int] | None:
    """
    שלב 4: זיהוי פקודת אישור/דחיית משלוח בטקסט.
    מחזיר (action, delivery_id) או None.
    תומך ב: "אשר משלוח 123", "דחה משלוח 123"
    """
    text = text.strip().replace("*", "")
    text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\ufeff]', '', text)
    text = re.sub(r'\s+', ' ', text)

    approve_match = re.match(
        r'^[✅✔️☑️\s]*(?:אשר|אישור)\s+משלוח\s+(\d+)\s*$', text
    )
    if approve_match:
        return ("approve", int(approve_match.group(1)))

    reject_match = re.match(
        r'^[❌✖️\s]*(?:דחה|דחייה|דחיה)\s+משלוח\s+(\d+)\s*$', text
    )
    if reject_match:
        return ("reject", int(reject_match.group(1)))

    return None


async def _handle_whatsapp_delivery_approval(
    db: AsyncSession,
    action: str,
    delivery_id: int,
    dispatcher_id: int,
) -> str:
    """שלב 4: ביצוע אישור/דחיית משלוח + הודעות"""
    from app.domain.services.shipment_workflow_service import ShipmentWorkflowService

    workflow = ShipmentWorkflowService(db)

    try:
        if action == "approve":
            success, msg, delivery = await workflow.approve_delivery(
                delivery_id, dispatcher_id
            )
        else:
            success, msg, delivery = await workflow.reject_delivery(
                delivery_id, dispatcher_id
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

    return msg


def _match_approval_command(text: str) -> tuple[str, int, str, str | None] | None:
    """
    זיהוי פקודת אישור/דחייה בטקסט.
    מחזיר (action, user_id, target_type, rejection_note) או None.
    target_type הוא "driver" אם צוין "נהג", אחרת "courier".
    rejection_note קיים רק בדחייה כשמנהל מוסיף טקסט אחרי המזהה.
    תומך באמוג'י שונים (✅✔️☑️), רווחים מרובים, וניקוד (כוכביות מ-WhatsApp).
    """
    # ניקוי: הסרת כוכביות (bold של WhatsApp), תווים בלתי-נראים (zero-width, RTL/LTR marks),
    # ורווחים עודפים — WhatsApp עשוי להזריק תווי Unicode בלתי-נראים
    text = text.strip().replace("*", "")
    text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\ufeff]', '', text)
    text = re.sub(r'\s+', ' ', text)

    approve_match = re.match(r'^[✅✔️☑️\s]*(?:אשר|אישור)(?:\s+(שליח|נהג))?\s+(\d+)\s*$', text)
    if approve_match:
        target_word = approve_match.group(1)
        target_type = "driver" if target_word == "נהג" else "courier"
        return ("approve", int(approve_match.group(2)), target_type, None)

    # דחייה — תמיכה בהערה אופציונלית אחרי המזהה
    reject_match = re.match(r'^[❌✖️\s]*(?:דחה|דחייה|דחיה)(?:\s+(שליח|נהג))?\s+(\d+)(?:\s+(.+))?\s*$', text)
    if reject_match:
        target_word = reject_match.group(1)
        target_type = "driver" if target_word == "נהג" else "courier"
        note = reject_match.group(3)
        note = (note.strip() or None) if note else None
        return ("reject", int(reject_match.group(2)), target_type, note)

    return None


async def _handle_whatsapp_approval(
    db: AsyncSession,
    action: str,
    target_id: int,
    admin_name: str,
    background_tasks: BackgroundTasks = None,
    rejection_note: str | None = None,
    target_type: str = "courier",
) -> str:
    """
    ביצוע אישור/דחייה + שליחת הודעה לשליח/נהג + סיכום לקבוצה.
    משותף לפקודות מקבוצה ומפרטי. תומך בשליחים (courier) ונהגים (driver).
    """
    if target_type == "driver":
        return await _handle_whatsapp_driver_approval(
            db, action, target_id, admin_name,
            background_tasks=background_tasks,
            rejection_note=rejection_note,
        )

    # שליח (courier) — ברירת מחדל
    if action == "approve":
        result = await CourierApprovalService.approve(db, target_id)
    else:
        result = await CourierApprovalService.reject(db, target_id, rejection_note=rejection_note)

    if not result.success:
        return result.message

    # הודעה לשליח וסיכום לקבוצה - ברקע כדי לא לחסום את ה-webhook
    from app.api.webhooks.telegram import send_telegram_message

    if background_tasks:
        background_tasks.add_task(
            CourierApprovalService.notify_after_decision,
            result.user,
            action,
            admin_name,
            send_telegram_fn=send_telegram_message,
            send_whatsapp_fn=send_whatsapp_message,
            rejection_note=rejection_note,
        )
    else:
        await CourierApprovalService.notify_after_decision(
            result.user,
            action,
            admin_name,
            send_telegram_fn=send_telegram_message,
            send_whatsapp_fn=send_whatsapp_message,
            rejection_note=rejection_note,
        )

    return result.message


async def _handle_whatsapp_driver_approval(
    db: AsyncSession,
    action: str,
    driver_user_id: int,
    admin_name: str,
    background_tasks: BackgroundTasks = None,
    rejection_note: str | None = None,
) -> str:
    """
    ביצוע אישור/דחייה של נהג iDriver מפקודת WhatsApp.
    משתמש בלוגיקה המשותפת מ-_apply_driver_decision_side_effects.
    """
    from app.domain.services.driver_verification_service import (
        DriverVerificationService,
    )
    from app.api.webhooks.telegram import _apply_driver_decision_side_effects

    if action == "approve":
        result = await DriverVerificationService.approve_driver(db, driver_user_id)
    else:
        result = await DriverVerificationService.reject_driver(
            db, driver_user_id, rejection_reason=rejection_note
        )

    if not result.success:
        return result.message

    if result.user and result.profile:
        decision = "approved" if action == "approve" else "rejected"
        await _apply_driver_decision_side_effects(
            db, background_tasks, driver_user_id, decision,
            admin_name, result.user, result.profile,
            rejection_note if action == "reject" else None,
        )

    return result.message


async def handle_admin_group_command(
    db: AsyncSession,
    text: str,
    background_tasks: BackgroundTasks = None,
) -> Optional[str]:
    """
    טיפול בפקודות מנהל מקבוצת הוואטסאפ (תאימות לאחור).
    מזהה פקודות כמו "אשר שליח 123" או "דחה שליח 456"
    """
    parsed = _match_approval_command(text)
    if not parsed:
        return None

    action, user_id, target_type, rejection_note = parsed
    return await _handle_whatsapp_approval(
        db,
        action,
        user_id,
        admin_name="מנהל (קבוצה)",
        background_tasks=background_tasks,
        rejection_note=rejection_note,
        target_type=target_type,
    )


async def handle_admin_private_command(
    db: AsyncSession,
    text: str,
    admin_name: str,
    background_tasks: BackgroundTasks = None,
) -> Optional[str]:
    """
    טיפול בפקודות אישור/דחייה מהודעות פרטיות של מנהלים.
    """
    parsed = _match_approval_command(text)
    if not parsed:
        return None

    action, user_id, target_type, rejection_note = parsed
    return await _handle_whatsapp_approval(
        db,
        action,
        user_id,
        admin_name=admin_name,
        background_tasks=background_tasks,
        rejection_note=rejection_note,
        target_type=target_type,
    )


async def _sender_fallback_wa(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
) -> tuple:
    """fallback לתפריט שולח — גרסת WhatsApp"""
    await state_manager.force_state(
        user.id, "whatsapp", SenderState.MENU.value, context={}
    )
    handler = SenderStateHandler(db)
    return await handler.handle_message(
        user_id=user.id, platform="whatsapp", message="תפריט"
    )


# עזרי admin context — ייבוא מקובץ משותף
from app.api.webhooks._admin_context import (
    inject_admin_return_button as _inject_admin_return_button_wa,
    save_admin_context as _save_admin_context_wa,
    restore_admin_context as _restore_admin_context_wa,
    restore_admin_role_and_route as _restore_admin_role_and_route_wa,
)


async def _route_to_role_menu_wa(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
) -> tuple:
    """
    ניתוב לתפריט הנכון לפי תפקיד — גרסת WhatsApp.

    חובה: כל תפקיד (UserRole) חייב להיות מטופל כאן במפורש.
    שמירת admin context: כמו בגרסת Telegram.
    """
    # שמירת admin context לפני ניתוב
    admin_keys = await _save_admin_context_wa(user.id, state_manager, "whatsapp")
    is_admin_impersonating = bool(
        admin_keys and admin_keys.get("original_role") == "admin"
    )
    # כשאדמין מחליף תפקיד, דילוג על בדיקת סדרן אלא אם ביקש תפקיד סדרן
    admin_target = admin_keys.get("admin_target_role") if is_admin_impersonating else None
    skip_dispatcher = admin_target is not None and admin_target != "dispatcher"

    response, new_state = await _route_to_role_menu_wa_inner(
        user, db, state_manager, skip_dispatcher_check=skip_dispatcher
    )

    # שחזור admin context והוספת כפתור חזרה
    if is_admin_impersonating:
        await _restore_admin_context_wa(
            user.id, state_manager, new_state, admin_keys, "whatsapp"
        )
        _inject_admin_return_button_wa(response)

    return response, new_state


async def _route_to_role_menu_wa_inner(
    user: User,
    db: AsyncSession,
    state_manager: StateManager,
    *,
    skip_dispatcher_check: bool = False,
) -> tuple:
    """ניתוב פנימי לתפריט לפי תפקיד — ללא טיפול ב-admin context (WhatsApp)"""
    if user.role == UserRole.COURIER:
        await state_manager.force_state(
            user.id, "whatsapp", CourierState.MENU.value, context={}
        )
        handler = CourierStateHandler(db, platform="whatsapp")
        return await handler.handle_message(user, "תפריט", None)

    if user.role == UserRole.STATION_OWNER:
        from app.domain.services.station_service import StationService

        station_service = StationService(db)
        station = await station_service.get_station_by_owner(user.id)

        if station:
            await state_manager.force_state(
                user.id, "whatsapp", StationOwnerState.MENU.value, context={}
            )
            handler = StationOwnerStateHandler(db, station.id, platform="whatsapp")
            return await handler.handle_message(user, "תפריט", None)
        # בעל תחנה ללא תחנה פעילה - הורדת תפקיד לשולח
        logger.warning(
            "Station owner without active station, downgrading to sender",
            extra_data={"user_id": user.id},
        )
        user.role = UserRole.SENDER
        await db.commit()
        return await _sender_fallback_wa(user, db, state_manager)

    if user.role == UserRole.DRIVER:
        from app.state_machine.driver_handler import DriverStateHandler
        from app.domain.services.driver_session_service import DriverSessionService

        # סשן 6: עדכון פעילות אחרונה — גם לנהג-סדרן
        session_service = DriverSessionService(db)
        await session_service.touch_session(user.id)

        # סשן 9: בדיקה אם הנהג הוא גם סדרן פעיל בתחנה
        if not skip_dispatcher_check:
            from app.domain.services.station_service import StationService

            station_service = StationService(db)
            dispatcher_station = await station_service.get_dispatcher_station(user.id)
            if dispatcher_station:
                await state_manager.force_state(
                    user.id, "whatsapp", DispatcherState.MENU.value, context={}
                )
                handler = DispatcherStateHandler(db, dispatcher_station.id, platform="whatsapp")
                return await handler.handle_message(user, "תפריט", None)

        await state_manager.force_state(
            user.id, "whatsapp", DriverState.INITIAL.value, context={}
        )
        handler = DriverStateHandler(db, platform="whatsapp")
        return await handler.handle_message(user, "תפריט", None)

    if user.role == UserRole.ADMIN:
        from app.core.config import settings as _settings

        if _settings.ADMIN_ROLE_SWITCH_ENABLED:
            from app.state_machine.admin_handler import AdminStateHandler
            from app.state_machine.states import AdminState

            await state_manager.force_state(
                user.id, "whatsapp", AdminState.MENU.value, context={}
            )
            handler = AdminStateHandler(db, platform="whatsapp")
            return await handler.handle_message(user, "תפריט", None)
        return await _sender_fallback_wa(user, db, state_manager)

    if user.role == UserRole.SENDER:
        # בדיקה אם המשתמש הוא סדרן פעיל — סדרנים שאינם שליחים נכנסים ישירות לתפריט סדרן
        if not skip_dispatcher_check:
            from app.domain.services.station_service import StationService

            station_service = StationService(db)
            dispatcher_station = await station_service.get_dispatcher_station(user.id)
            if dispatcher_station:
                await state_manager.force_state(
                    user.id, "whatsapp", DispatcherState.MENU.value, context={}
                )
                handler = DispatcherStateHandler(db, dispatcher_station.id, platform="whatsapp")
                return await handler.handle_message(user, "תפריט", None)

        return await _sender_fallback_wa(user, db, state_manager)

    # תפקיד לא מוכר
    logger.warning(
        "Unknown user role in menu routing, falling back to sender",
        extra_data={"user_id": user.id, "role": str(user.role)},
    )
    return await _sender_fallback_wa(user, db, state_manager)


# ──────────────────────────────────────────────
#  לוגיקה משותפת אחרי handler.handle_message() עבור שליחים
#  נקרא מ-WPPConnect, Cloud API ו-Telegram webhooks
# ──────────────────────────────────────────────


async def _handle_courier_post_processing(
    db: AsyncSession,
    user: User,
    previous_state: str | None,
    new_state: str | None,
    contact_phone: str,
    photo_file_id: str | None,
    platform: str,
    background_tasks: BackgroundTasks,
) -> None:
    """
    לוגיקה משותפת אחרי טיפול בהודעת שליח — כרטיס נהג + הפקדה.

    כולל idempotency check למניעת שליחה כפולה.
    """
    # שליחת "כרטיס נהג" למנהלים רק במעבר הראשון למצב PENDING_APPROVAL
    # בדיקת idempotency מונעת שליחה כפולה גם במקרה של race condition
    # (למשל אם הגטוויי שולח את אותה לחיצת כפתור כשני webhook calls נפרדים)
    if (
        new_state == CourierState.PENDING_APPROVAL.value
        and previous_state != CourierState.PENDING_APPROVAL.value
        and user.approval_status == ApprovalStatus.PENDING
    ):
        # מפתח idempotency כולל את מועד אישור התקנון (לדקה הקרובה).
        # - שני webhook calls מקבילים לאותו רישום → אותה דקה → אותו מפתח → חסימה
        # - רי-רגיסטרציה אחרי דחייה → terms_accepted_at חדש → מפתח שונה → מאפשר
        reg_ts = int(user.terms_accepted_at.timestamp()) // 60 if user.terms_accepted_at else 0
        notify_key = f"courier_reg_notify_{user.id}_{reg_ts}"
        should_notify = await _try_acquire_message(db, notify_key, "notification")
        if should_notify:
            background_tasks.add_task(
                AdminNotificationService.notify_new_courier_registration,
                user.id,
                user.full_name or user.name or "לא צוין",
                user.service_area or "לא צוין",
                contact_phone,
                user.id_document_url,
                platform,
                user.vehicle_category,
                user.selfie_file_id,
                user.vehicle_photo_file_id,
            )
            await _mark_message_completed(db, notify_key)
        else:
            logger.info(
                "כרטיס נהג כבר נשלח, מדלג על שליחה כפולה",
                extra_data={"user_id": user.id},
            )

    # צילום מסך להפקדה — הודעה למנהלים
    if photo_file_id:
        state_manager = StateManager(db)
        context = await state_manager.get_context(user.id, platform)
        if context.get("deposit_screenshot"):
            background_tasks.add_task(
                AdminNotificationService.notify_deposit_request,
                user.id,
                user.full_name or user.name or "לא ידוע",
                contact_phone,
                photo_file_id,
                platform,
            )


async def send_welcome_message(phone_number: str):
    """הודעת ברוכים הבאים ותפריט ראשי [שלב 1]"""
    welcome_text = (
        "ברוכים הבאים למשלוח בצ'יק 🚚\n"
        "המערכת החכמה לשיתוף משלוחים.\n\n"
        "איך נוכל לעזור היום?\n\n"
        "בכל שלב תוכלו לחזור לתפריט הראשי על ידי הקשה של #"
    )

    keyboard = [
        ["🚚 הצטרפות למנוי וקבלת משלוחים"],
        ["🚗 הצטרפות כנהג"],
        ["📦 העלאת משלוח מהיר"],
        ["🏪 הצטרפות כתחנה"],
        ["📞 פנייה לניהול"],
    ]
    await send_whatsapp_message(phone_number, welcome_text, keyboard)


@router.post(
    "/webhook",
    summary="Webhook - WhatsApp (קבלת הודעות נכנסות)",
    description=(
        "נקודת כניסה לקבלת הודעות מ-WhatsApp Gateway. "
        "מבצעת ניתוב לזרימת שולח/שליח לפי role ומנהלת state machine."
    ),
    responses={
        200: {"description": "הודעה התקבלה ועובדה"},
        403: {"description": "חתימה לא תקינה"},
        429: {"description": "IP חסום עקב ניסיונות אימות כושלים"},
    },
)
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    payload: WhatsAppWebhookPayload = Depends(_get_verified_payload),
):
    """
    Handle incoming WhatsApp messages.
    Routes to sender or courier handlers based on user role.
    """

    responses = []

    for message in payload.messages:
        # מניעת עיבוד כפול — בדיקה מול טבלת idempotency ב-DB
        # עטוף ב-try כדי שכשל ב-idempotency (למשל DataError) לא יעצור הודעות הבאות
        try:
            if not await _try_acquire_message(db, message.message_id, "whatsapp"):
                continue
        except Exception:
            logger.error(
                "Idempotency check failed, skipping message",
                extra_data={"message_id": message.message_id},
                exc_info=True,
            )
            continue

        _msg_failed = False
        try:
            text = message.text or ""
            sender_id = (message.sender_id or message.from_number or "").strip()
            reply_to = (message.reply_to or message.from_number or "").strip()
            from_number = (message.from_number or "").strip()
            resolved_phone = (message.resolved_phone or "").strip()
            # תמונות רגילות (media_type מכיל 'image')
            # או מסמך שהוא בעצם תמונה (media_type=document + mime_type מתחיל ב-image/)
            if message.media_url and message.media_type:
                mt = message.media_type.lower()
                if "image" in mt:
                    photo_file_id = message.media_url
                elif 'document' in mt and message.mime_type and message.mime_type.lower().startswith('image/'):
                    photo_file_id = message.media_url
                else:
                    photo_file_id = None
            else:
                photo_file_id = None

            # מיקום GPS (iDriver סשן 5 — חיפוש לפי מיקום)
            location_lat: float | None = message.location_latitude
            location_lng: float | None = message.location_longitude

            logger.debug(
                "WhatsApp message received",
                extra_data={
                    "from": PhoneNumberValidator.mask(sender_id),
                    "reply_to": PhoneNumberValidator.mask(reply_to),
                    "text_preview": text[:50] if text else "",
                    "media_type": message.media_type,
                    "has_media_url": bool(message.media_url),
                },
            )
    
            # Skip empty messages (מיקום GPS גם נחשב תוכן — לחיפוש נסיעות)
            if not text and not photo_file_id and location_lat is None:
                continue
    
            # בדיקה אם ההודעה מגיעה מקבוצה (group ID מסתיים ב-@g.us)
            is_group_message = sender_id.endswith("@g.us")
    
            if is_group_message:
                # בדיקה אם זו קבוצת המנהלים
                if (
                    settings.WHATSAPP_ADMIN_GROUP_ID
                    and sender_id == settings.WHATSAPP_ADMIN_GROUP_ID
                ):
                    logger.info(
                        "Admin group message received",
                        extra_data={"group_id": sender_id, "text": text[:50]},
                    )
    
                    # ניסיון לזהות פקודת מנהל
                    response_text = await handle_admin_group_command(
                        db, text, background_tasks=background_tasks
                    )
    
                    if response_text:
                        # שליחת תגובה לקבוצה
                        background_tasks.add_task(
                            send_whatsapp_message, sender_id, response_text  # שליחה לקבוצה
                        )
                        responses.append(
                            {
                                "from": sender_id,
                                "response": response_text,
                                "admin_command": True,
                            }
                        )
                    else:
                        # הודעה רגילה בקבוצה (לא פקודה) - מתעלמים
                        logger.debug("Non-command message in admin group, ignoring")
    
                else:
                    # הודעה מקבוצה אחרת - מתעלמים
                    logger.debug(
                        "Message from non-admin group, ignoring",
                        extra_data={"group_id": sender_id},
                    )
    
                continue  # לא ממשיכים לטיפול רגיל בהודעות מקבוצות
    
            # Get or create user
            user, is_new_user, _normalized_phone = await get_or_create_user(
                db,
                sender_id,
                from_number=from_number,
                reply_to=reply_to,
                resolved_phone=resolved_phone,
            )

            # לוג זיהוי משתמש — observability למעקב אחר חיפוש/יצירה
            logger.info(
                "User resolved",
                extra_data={
                    "resolved_user_id": user.id,
                    "lookup_by": "whatsapp",
                    "sender_id": PhoneNumberValidator.mask(sender_id) if sender_id else None,
                    "normalized_phone": PhoneNumberValidator.mask(_normalized_phone) if _normalized_phone else None,
                    "is_new": is_new_user,
                    "role": user.role.value if user.role else None,
                },
            )

            # טיפול בפקודות אישור/דחייה מהודעות פרטיות של מנהלים
            # חייב להיות לפני בדיקת is_new_user כדי שמנהל חדש שעוד לא ב-DB
            # יוכל לאשר/לדחות שליחים כבר מההודעה הראשונה שלו.
            # בודקים גם resolved_phone (טלפון שהגטוויי חילץ מ-LID) וגם phone_number מה-DB
            # (במקרה שהמשתמש נוצר לפני שהגטוויי עבר ל-LID).
            is_admin_sender = _is_whatsapp_admin_any(
                sender_id, reply_to, from_number, resolved_phone, user.phone_number
            )
            if is_admin_sender and text:
                admin_response = await handle_admin_private_command(
                    db,
                    text,
                    admin_name=user.name or PhoneNumberValidator.mask(sender_id),
                    background_tasks=background_tasks,
                )
                if admin_response:
                    # שליחת התגובה למספר המנהל מההגדרות (שאנחנו יודעים שעובד)
                    # במקום ל-reply_to (שעלול להיות @lid שהגטוויי לא יודע לשלוח אליו)
                    admin_send_to = _resolve_admin_send_target(
                        sender_id, reply_to, from_number, resolved_phone
                    )
                    background_tasks.add_task(send_whatsapp_message, admin_send_to, admin_response)
                    responses.append({
                        "from": sender_id,
                        "response": admin_response,
                        "admin_command": True
                    })
                    continue

            # שלב 4: טיפול בפקודות אישור/דחיית משלוח (סדרנים)
            if text and not is_new_user:
                delivery_approval = _match_delivery_approval_command(text)
                if delivery_approval:
                    action, delivery_id = delivery_approval

                    # שליפת המשלוח לבדיקת תחנה
                    from app.domain.services.station_service import StationService
                    from app.db.models.delivery import Delivery
                    station_service = StationService(db)

                    delivery_result = await db.execute(
                        select(Delivery).where(Delivery.id == delivery_id)
                    )
                    target_delivery = delivery_result.scalar_one_or_none()

                    # בדיקה שהמשלוח קיים ושייך לתחנה
                    if not target_delivery or not target_delivery.station_id:
                        background_tasks.add_task(
                            send_whatsapp_message, reply_to,
                            "❌ המשלוח לא נמצא."
                        )
                        responses.append({
                            "from": sender_id,
                            "response": "❌ המשלוח לא נמצא.",
                            "delivery_approval": True,
                        })
                        continue

                    # בדיקה שהסדרן שייך לתחנה של המשלוח הספציפי
                    is_disp = await station_service.is_dispatcher_of_station(
                        user.id, target_delivery.station_id
                    )
                    if not is_disp:
                        background_tasks.add_task(
                            send_whatsapp_message, reply_to,
                            "❌ אין לך הרשאה לאשר/לדחות משלוחים בתחנה זו."
                        )
                        responses.append({
                            "from": sender_id,
                            "response": "❌ אין לך הרשאה לאשר/לדחות משלוחים בתחנה זו.",
                            "delivery_approval": True,
                        })
                        continue

                    approval_msg = await _handle_whatsapp_delivery_approval(
                        db, action, delivery_id,
                        dispatcher_id=user.id,
                    )
                    background_tasks.add_task(
                        send_whatsapp_message, reply_to, approval_msg
                    )
                    responses.append({
                        "from": sender_id,
                        "response": approval_msg,
                        "delivery_approval": True,
                    })
                    continue

            # Initialize state manager
            state_manager = StateManager(db)
    
            # New user - show welcome message with role selection [1.1]
            if is_new_user:
                background_tasks.add_task(send_welcome_message, reply_to)
                responses.append(
                    {"from": sender_id, "response": "welcome", "new_user": True}
                )
                continue
    
            # Handle "#" to return to main menu
            if text.strip() in {"#", "תפריט ראשי"}:
                # רענון מהDB לפני בדיקת סטטוס - למניעת stale data אם האדמין אישר בינתיים
                await db.refresh(user)
                # לוג לדיבאג - מראה את מצב המשתמש בלחיצה על #
                logger.info(
                    "User pressed # to return to menu",
                    extra_data={
                        "user_id": user.id,
                        "phone": PhoneNumberValidator.mask(sender_id),
                        "role": user.role.value if user.role else None,
                        "approval_status": (
                            user.approval_status.value if user.approval_status else None
                        ),
                    },
                )
    
                # אדמין (לפי WHATSAPP_ADMIN_NUMBERS): מאפשרים יציאה "קשיחה" מכל זרימה וחזרה לתפריט הראשי
                # של כל אפשרויות הרישום.
                if is_admin_sender:
                    # שחזור תפקיד לשולח כדי שהודעות הבאות לא יגיעו ל-CourierStateHandler
                    if user.role == UserRole.COURIER:
                        user.role = UserRole.SENDER
                        await db.commit()
    
                    # איפוס state כדי לאפשר עבודה עם תפריט ראשי גם אם האדמין היה באמצע זרימה רב-שלבית כשליח
                    await state_manager.force_state(
                        user.id,
                        "whatsapp",
                        SenderState.MENU.value,
                        context={"admin_root_menu": True},
                    )
    
                    # שליחה למספר המנהל מההגדרות (reply_to עלול להיות @lid)
                    admin_send_to = _resolve_admin_send_target(
                        sender_id, reply_to, from_number, resolved_phone
                    )
                    background_tasks.add_task(send_welcome_message, admin_send_to)
                    responses.append(
                        {
                            "from": sender_id,
                            "response": "welcome (admin main menu)",
                            "new_state": SenderState.MENU.value,
                            "admin_main_menu": True,
                        }
                    )
                    continue
    
                # Reset state to menu
                if user.role == UserRole.COURIER:
                    # בדיקה אם המשתמש נכנס לזרימת שליח מתפריט אדמין
                    # (fallback למקרה שזיהוי אדמין לפי מספר טלפון נכשל, למשל בגלל LID)
                    _hash_ctx = await state_manager.get_context(user.id, "whatsapp")
                    _entered_as_admin = _hash_ctx.get("entered_as_admin", False)
    
                    if user.approval_status != ApprovalStatus.APPROVED or _entered_as_admin:
                        # שליח לא מאושר / אדמין שנכנס לזרימת שליח - מחזירים לתפריט ראשי
                        logger.info(
                            "Courier pressed #, switching to sender",
                            extra_data={
                                "user_id": user.id,
                                "phone": PhoneNumberValidator.mask(sender_id),
                                "reply_to": PhoneNumberValidator.mask(reply_to),
                                "entered_as_admin": _entered_as_admin,
                                "approval_status": (
                                    user.approval_status.value if user.approval_status else None
                                ),
                            },
                        )
                        user.role = UserRole.SENDER
                        await db.commit()
                        await state_manager.force_state(
                            user.id, "whatsapp", SenderState.MENU.value, context={}
                        )
                        # אם נכנס כאדמין, שליחה ליעד מנהל (reply_to עלול להיות LID)
                        _send_to = (
                            _resolve_admin_send_target(
                                sender_id, reply_to, from_number, resolved_phone
                            )
                            if _entered_as_admin
                            else reply_to
                        )
                        background_tasks.add_task(send_welcome_message, _send_to)
                        responses.append(
                            {
                                "from": sender_id,
                                "response": "welcome (switched from courier to sender)",
                                "new_state": SenderState.MENU.value,
                            }
                        )
                        continue
    
                response, new_state = await _route_to_role_menu_wa(user, db, state_manager)
    
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue
    
            # טיפול בכפתורי תפריט ראשי [שלב 1]
            # הכפתורים פעילים רק למשתמשים שאינם באמצע זרימה רב-שלבית
            # (רישום שליח, זרימת סדרן, זרימת בעל תחנה)
            _current_state_value = await state_manager.get_current_state(
                user.id, "whatsapp"
            )
            _is_courier_in_registration = (
                user.role == UserRole.COURIER
                and _current_state_value
                in {
                    CourierState.REGISTER_COLLECT_NAME.value,
                    CourierState.REGISTER_COLLECT_DOCUMENT.value,
                    CourierState.REGISTER_COLLECT_SELFIE.value,
                    CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value,
                    CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value,
                    CourierState.REGISTER_TERMS.value,
                }
            )
            _is_in_multi_step_flow = _is_courier_in_registration or (
                isinstance(_current_state_value, str)
                and (
                    _current_state_value.startswith(("DISPATCHER.", "STATION.", "DRIVER.", "ADMIN."))
                    # הגנה על זרימות שולח: מונע "תחנה" וכו' מלתפוס כתובות כמו "תחנה מרכזית"
                    or (
                        _current_state_value.startswith("SENDER.")
                        and _current_state_value != SenderState.MENU.value
                    )
                )
            )
            _context = await state_manager.get_context(user.id, "whatsapp")
            _admin_root_menu = bool(_context.get("admin_root_menu")) and is_admin_sender

            # חזרה לאדמין — אדמין שהחליף תפקיד רוצה לחזור
            if "חזרה לאדמין" in text and _context.get("original_role") == "admin":
                original_approval = _context.get("original_approval_status")
                user.role = UserRole.ADMIN
                if original_approval is not None:
                    user.approval_status = ApprovalStatus(original_approval) if original_approval else None
                else:
                    user.approval_status = None
                await db.commit()

                from app.state_machine.admin_handler import AdminStateHandler
                from app.state_machine.states import AdminState

                await state_manager.force_state(
                    user.id, "whatsapp", AdminState.MENU.value,
                    context={
                        "original_role": None,
                        "original_approval_status": None,
                        "admin_station_id": None,
                        "admin_target_role": None,
                    },
                )
                admin_handler = AdminStateHandler(db, platform="whatsapp")
                response, new_state = await admin_handler.handle_message(user, "תפריט", None)
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                continue

            if not _is_in_multi_step_flow:
                if (
                    user.role in (UserRole.SENDER, UserRole.ADMIN) or _admin_root_menu
                ) and ("הצטרפות למנוי" in text or "שליח" in text):
                    # ניתוב לתהליך הרישום כנהג/שליח
                    user.role = UserRole.COURIER
                    await db.commit()
    
                    # שמירת דגל אדמין בקונטקסט כדי לאפשר חזרה לתפריט ראשי גם אם זיהוי אדמין נכשל
                    courier_context = {}
                    if _admin_root_menu or is_admin_sender:
                        courier_context["entered_as_admin"] = True
    
                    await state_manager.force_state(
                        user.id, "whatsapp", CourierState.INITIAL.value, context=courier_context
                    )
    
                    handler = CourierStateHandler(db, platform="whatsapp")
                    response, new_state = await handler.handle_message(
                        user, text, photo_file_id
                    )
    
                    background_tasks.add_task(
                        send_whatsapp_message, reply_to, response.text, response.keyboard
                    )
                    responses.append(
                        {
                            "from": sender_id,
                            "response": response.text,
                            "new_state": new_state,
                        }
                    )
                    continue
    
                if (
                    user.role in (UserRole.SENDER, UserRole.ADMIN) or _admin_root_menu
                ) and ("הצטרפות כנהג" in text or "נהג" in text):
                    # ניתוב לתהליך רישום כנהג (iDriver)
                    from app.state_machine.driver_handler import DriverStateHandler

                    user.role = UserRole.DRIVER
                    await db.commit()

                    # שמירת דגל אדמין בקונטקסט כדי לאפשר חזרה לתפריט ראשי גם אם זיהוי אדמין נכשל
                    driver_context: dict = {}
                    if _admin_root_menu or is_admin_sender:
                        driver_context["entered_as_admin"] = True

                    await state_manager.force_state(
                        user.id, "whatsapp", DriverState.INITIAL.value, context=driver_context
                    )

                    handler = DriverStateHandler(db, platform="whatsapp")
                    response, new_state = await handler.handle_message(
                        user, text, photo_file_id
                    )

                    background_tasks.add_task(
                        send_whatsapp_message, reply_to, response.text, response.keyboard
                    )
                    responses.append(
                        {
                            "from": sender_id,
                            "response": response.text,
                            "new_state": new_state,
                        }
                    )
                    continue

                if ("העלאת משלוח מהיר" in text or "משלוח מהיר" in text) and (
                    user.role in (UserRole.SENDER, UserRole.ADMIN) or _admin_root_menu
                ):
                    # קישור חיצוני לקבוצת WhatsApp
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
                    responses.append(
                        {"from": sender_id, "response": msg_text, "new_state": None}
                    )
                    continue
    
                if ("הצטרפות כתחנה" in text or "תחנה" in text) and (
                    user.role in (UserRole.SENDER, UserRole.ADMIN) or _admin_root_menu
                ):
                    # הודעה שיווקית עבור תחנות
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
                    responses.append(
                        {"from": sender_id, "response": station_text, "new_state": None}
                    )
                    continue
    
    
                # כל תפקיד שנמצא בזרימת סדרן מוחרג — מנוהל דרך בלוק DISPATCHER למטה
                _in_dispatcher_flow = (
                    isinstance(_current_state_value, str)
                    and _current_state_value.startswith("DISPATCHER.")
                )
                if "חזרה לתפריט" in text and (
                    (
                        user.role not in (UserRole.COURIER, UserRole.STATION_OWNER)
                        and not _in_dispatcher_flow
                    )
                    or _admin_root_menu
                ):
                    # כפתור "חזרה לתפריט" - שולחים רגילים חוזרים לתפריט הראשי
                    background_tasks.add_task(send_welcome_message, reply_to)
                    responses.append(
                        {"from": sender_id, "response": "welcome", "new_state": None}
                    )
                    continue
    
            # פנייה לניהול — פתוח לכל התפקידים, ללא תלות ב-guard של זרימה רב-שלבית
            if "פנייה לניהול" in text:
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
                responses.append(
                    {"from": sender_id, "response": admin_text, "new_state": None}
                )
                continue

            # העברת הודעה להנהלה — אם המשתמש לחץ "פנייה לניהול" בהודעה הקודמת
            if _context.get("contact_admin_pending"):
                # ניקוי הדגל מהקונטקסט
                await state_manager.update_context(
                    user.id, "whatsapp", "contact_admin_pending", False
                )

                # כפתור חזרה → לא להעביר, פשוט לחזור לתפריט
                if "חזרה" in text or "תפריט" in text:
                    response, new_state = await _route_to_role_menu_wa(
                        user, db, state_manager
                    )
                    background_tasks.add_task(
                        send_whatsapp_message, reply_to, response.text, response.keyboard
                    )
                    responses.append(
                        {"from": sender_id, "response": response.text, "new_state": new_state}
                    )
                    continue

                # העברת ההודעה למנהלים
                user_name = user.full_name or user.name or "לא צוין"
                forward_text = (
                    f"📨 פנייה מ-{user_name}\n"
                    f"({PhoneNumberValidator.mask(reply_to)})\n\n"
                    f"{text}"
                )

                from app.domain.services.admin_notification_service import (
                    AdminNotificationService,
                    _parse_csv_setting,
                )

                sent = False
                # ניסיון שליחה לקבוצת אדמינים בוואטסאפ
                if settings.WHATSAPP_ADMIN_GROUP_ID:
                    sent = await AdminNotificationService._send_whatsapp_admin_message(
                        settings.WHATSAPP_ADMIN_GROUP_ID, forward_text
                    )
                # fallback: שליחה למנהלים פרטיים בוואטסאפ
                if not sent:
                    wa_admins = _parse_csv_setting(settings.WHATSAPP_ADMIN_NUMBERS)
                    for admin_phone in wa_admins:
                        sent = await AdminNotificationService._send_whatsapp_admin_message(
                            admin_phone, forward_text
                        ) or sent
                # fallback: שליחה לקבוצת טלגרם
                if not sent and settings.TELEGRAM_ADMIN_CHAT_ID:
                    sent = await AdminNotificationService._send_telegram_message(
                        settings.TELEGRAM_ADMIN_CHAT_ID, forward_text
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

                background_tasks.add_task(send_whatsapp_message, reply_to, confirm_text)
                responses.append(
                    {"from": sender_id, "response": confirm_text, "new_state": None}
                )
                continue

            # ==================== ניתוב לפי תפקיד [שלב 3] ====================
    
            current_state = _current_state_value
    
            # ניתוב לבעל תחנה [שלב 3.3]
            if user.role == UserRole.STATION_OWNER:
                from app.domain.services.station_service import StationService
    
                station_service = StationService(db)
                station = await station_service.get_station_by_owner(user.id)
    
                if station:
                    handler = StationOwnerStateHandler(db, station.id, platform="whatsapp")
                    response, new_state = await handler.handle_message(
                        user, text, photo_file_id
                    )
                else:
                    # בעל תחנה ללא תחנה פעילה - fallback
                    response, new_state = await _route_to_role_menu_wa(
                        user, db, state_manager
                    )
    
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue
    
            # ניתוב לתפריט סדרן (כפתור "תפריט סדרן" — פתוח לכל תפקיד שהוא סדרן פעיל) [שלב 3.2]
            # בדיקת keyword רק כשהמשתמש לא באמצע זרימת סדרן — מונע תפיסת טקסט חופשי כלחיצת כפתור
            _in_dispatcher_flow = isinstance(current_state, str) and current_state.startswith("DISPATCHER.")
            if not _in_dispatcher_flow and ("תפריט סדרן" in text or "🏪 תפריט סדרן" in text):
                from app.domain.services.station_service import StationService
    
                station_service = StationService(db)
                station = await station_service.get_dispatcher_station(user.id)
    
                if station:
                    _dm_admin_keys_wa = await _save_admin_context_wa(
                        user.id, state_manager, "whatsapp"
                    )
                    await state_manager.force_state(
                        user.id, "whatsapp", DispatcherState.MENU.value, context={}
                    )
                    handler = DispatcherStateHandler(db, station.id, platform="whatsapp")
                    response, new_state = await handler.handle_message(user, "תפריט", None)
                    # שחזור admin context אחרי ה-handler
                    if _dm_admin_keys_wa:
                        await _restore_admin_context_wa(
                            user.id, state_manager, new_state,
                            _dm_admin_keys_wa, "whatsapp",
                        )
                    if _dm_admin_keys_wa and _dm_admin_keys_wa.get("original_role") == "admin":
                        _inject_admin_return_button_wa(response)
                else:
                    # סדרן הוסר או תחנה בוטלה
                    logger.warning(
                        "Dispatcher clicked station menu but station not found",
                        extra_data={"user_id": user.id},
                    )
                    response, new_state = await _route_to_role_menu_wa(
                        user, db, state_manager
                    )
    
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue
    
            # אם המשתמש באמצע זרימת סדרן - ממשיכים עם DispatcherStateHandler
            if current_state and current_state.startswith("DISPATCHER."):
                from app.domain.services.station_service import StationService
    
                station_service = StationService(db)
                station = await station_service.get_dispatcher_station(user.id)
    
                if station:
                    # כפתור "חזרה לתפריט ראשי"/"חזרה לתפריט נהג" — חזרה לתפריט לפי תפקיד
                    # חשוב: קוראים ישירות ל-fallback ולא ל-_route_to_role_menu_wa כדי למנוע
                    # לולאה (כי _route_to_role_menu_wa יזהה שהמשתמש סדרן ויחזיר לתפריט סדרן)
                    if "חזרה לתפריט נהג" in text or "חזרה לתפריט ראשי" in text:
                        # אדמין שהחליף תפקיד — שחזור ישיר לתפריט אדמין
                        # (לא _route_to_role_menu_wa — כי הוא יזהה סדרן ויחזור ללולאה)
                        _back_ctx_wa = await state_manager.get_context(
                            user.id, "whatsapp"
                        )
                        if _back_ctx_wa.get("original_role") == "admin":
                            response, new_state = await _restore_admin_role_and_route_wa(
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
                        elif user.role == UserRole.DRIVER:
                            # סשן 9: נהג-סדרן חוזר לתפריט נהג (לא סדרן)
                            from app.state_machine.driver_handler import DriverStateHandler
                            from app.domain.services.driver_session_service import DriverSessionService

                            session_service = DriverSessionService(db)
                            await session_service.touch_session(user.id)

                            await state_manager.force_state(
                                user.id, "whatsapp", DriverState.INITIAL.value, context={}
                            )
                            handler = DriverStateHandler(db, platform="whatsapp")
                            response, new_state = await handler.handle_message(
                                user, "תפריט", None
                            )
                        else:
                            response, new_state = await _sender_fallback_wa(
                                user, db, state_manager
                            )
                    else:
                        handler = DispatcherStateHandler(
                            db, station.id, platform="whatsapp"
                        )
                        response, new_state = await handler.handle_message(
                            user, text, photo_file_id
                        )
                        # הוספת כפתור "חזרה לאדמין" אם נדרש
                        _disp_ctx_wa = await state_manager.get_context(
                            user.id, "whatsapp"
                        )
                        if _disp_ctx_wa.get("original_role") == "admin":
                            _inject_admin_return_button_wa(response)
                else:
                    # תחנה לא נמצאה - איפוס לתפריט נהג
                    logger.warning(
                        "Dispatcher station not found, resetting to courier menu",
                        extra_data={"user_id": user.id, "state": current_state},
                    )
                    response, new_state = await _route_to_role_menu_wa(
                        user, db, state_manager
                    )
    
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue
    
            # אם המשתמש באמצע זרימת בעל תחנה - ממשיכים
            if current_state and current_state.startswith("STATION."):
                from app.domain.services.station_service import StationService
    
                station_service = StationService(db)
                station = await station_service.get_station_by_owner(user.id)
    
                if station:
                    handler = StationOwnerStateHandler(db, station.id, platform="whatsapp")
                    response, new_state = await handler.handle_message(
                        user, text, photo_file_id
                    )
                    # הוספת כפתור "חזרה לאדמין" אם נדרש
                    _station_ctx_wa = await state_manager.get_context(user.id, "whatsapp")
                    if _station_ctx_wa.get("original_role") == "admin":
                        _inject_admin_return_button_wa(response)
                else:
                    # תחנה לא נמצאה - fallback
                    response, new_state = await _route_to_role_menu_wa(
                        user, db, state_manager
                    )

                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue
    
            # ניתוב אדמין — תפריט אדמין או המשך זרימת בחירת תפקיד
            if user.role == UserRole.ADMIN:
                from app.core.config import settings as _wa_settings

                if _wa_settings.ADMIN_ROLE_SWITCH_ENABLED:
                    from app.state_machine.admin_handler import AdminStateHandler
                    from app.state_machine.states import AdminState

                    is_admin_flow = isinstance(current_state, str) and current_state.startswith("ADMIN.")
                    if not is_admin_flow:
                        await state_manager.force_state(
                            user.id, "whatsapp", AdminState.MENU.value, context={}
                        )
                    _admin_handler = AdminStateHandler(db, platform="whatsapp")
                    response, new_state = await _admin_handler.handle_message(user, text, photo_file_id)

                    # מצב מיוחד: admin_handler מחזיר _ADMIN_SWITCH_* כשצריך לנתב לתפקיד חדש
                    if isinstance(new_state, str) and new_state.startswith("_ADMIN_SWITCH_"):
                        background_tasks.add_task(
                            send_whatsapp_message, reply_to, response.text, response.keyboard
                        )
                        # _route_to_role_menu_wa שומר ומשחזר admin context אוטומטית,
                        # ומוסיף כפתור "חזרה לאדמין" לתגובה
                        response2, new_state2 = await _route_to_role_menu_wa(user, db, state_manager)
                        background_tasks.add_task(
                            send_whatsapp_message, reply_to, response2.text, response2.keyboard
                        )
                        responses.append(
                            {"from": sender_id, "response": response2.text, "new_state": new_state2}
                        )
                        continue

                    background_tasks.add_task(
                        send_whatsapp_message, reply_to, response.text, response.keyboard
                    )
                    responses.append(
                        {"from": sender_id, "response": response.text, "new_state": new_state}
                    )
                    continue

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
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue

            # Route based on user role
            if user.role == UserRole.COURIER:
                # שמירת המצב הקודם לפני הטיפול בהודעה
                previous_state = current_state

                handler = CourierStateHandler(db, platform="whatsapp")
                response, new_state = await handler.handle_message(
                    user, text, photo_file_id
                )

                # לוגיקה משותפת: כרטיס נהג + הפקדה
                contact_phone = _resolve_contact_phone(
                    resolved_phone=resolved_phone,
                    from_number=from_number,
                    reply_to=reply_to,
                    sender_id=sender_id,
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
                _courier_ctx_wa = await state_manager.get_context(user.id, "whatsapp")
                if _courier_ctx_wa.get("original_role") == "admin":
                    _inject_admin_return_button_wa(response)
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue

            if user.role == UserRole.DRIVER:
                # iDriver — ניתוב נהג ל-handler (סשנים 2-6)
                from app.state_machine.driver_handler import DriverStateHandler as _DH
                from app.domain.services.driver_session_service import DriverSessionService as _DSS

                # סשן 6: עדכון פעילות אחרונה בכל הודעה מנהג
                _session_svc = _DSS(db)
                await _session_svc.touch_session(user.id)

                is_driver_flow = isinstance(current_state, str) and current_state.startswith("DRIVER.")
                _drv_admin_keys_wa = None
                if not is_driver_flow:
                    _drv_admin_keys_wa = await _save_admin_context_wa(
                        user.id, state_manager, "whatsapp"
                    )
                    await state_manager.force_state(
                        user.id, "whatsapp", DriverState.INITIAL.value, context={}
                    )
                _driver_handler = _DH(db, platform="whatsapp")
                response, new_state = await _driver_handler.handle_message(
                    user, text, None,
                    location_lat=location_lat, location_lng=location_lng,
                )
                # שחזור admin context אחרי ה-handler
                if _drv_admin_keys_wa:
                    await _restore_admin_context_wa(
                        user.id, state_manager, new_state,
                        _drv_admin_keys_wa, "whatsapp",
                    )
                # הוספת כפתור "חזרה לאדמין" אם נדרש
                if _drv_admin_keys_wa and _drv_admin_keys_wa.get("original_role") == "admin":
                    _inject_admin_return_button_wa(response)
                else:
                    _driver_ctx_wa = await state_manager.get_context(user.id, "whatsapp")
                    if _driver_ctx_wa.get("original_role") == "admin":
                        _inject_admin_return_button_wa(response)
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue

            # Sender flow
            if "שלוח" in text or "חבילה" in text:
                handler = SenderStateHandler(db)
                response, new_state = await handler.handle_message(
                    user_id=user.id, platform="whatsapp", message=text
                )
                # הוספת כפתור "חזרה לאדמין" אם נדרש
                _sender_ctx_wa = await state_manager.get_context(user.id, "whatsapp")
                if _sender_ctx_wa.get("original_role") == "admin":
                    _inject_admin_return_button_wa(response)
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue

            # If user is in the middle of a sender flow, continue it
            if (
                current_state
                and not current_state.startswith("COURIER.")
                and not current_state.startswith("DISPATCHER.")
                and not current_state.startswith("STATION.")
                and not current_state.startswith("DRIVER.")
                and not current_state.startswith("ADMIN.")
                and current_state not in ["INITIAL", "SENDER.INITIAL"]
            ):
                handler = SenderStateHandler(db)
                response, new_state = await handler.handle_message(
                    user_id=user.id, platform="whatsapp", message=text
                )
                # הוספת כפתור "חזרה לאדמין" אם נדרש
                _sender_ctx2_wa = await state_manager.get_context(user.id, "whatsapp")
                if _sender_ctx2_wa.get("original_role") == "admin":
                    _inject_admin_return_button_wa(response)
                background_tasks.add_task(
                    send_whatsapp_message, reply_to, response.text, response.keyboard
                )
                responses.append(
                    {"from": sender_id, "response": response.text, "new_state": new_state}
                )
                continue
    
            # Default: show welcome message with role selection
            background_tasks.add_task(send_welcome_message, reply_to)
            responses.append({"from": sender_id, "response": "welcome", "new_state": None})

        except Exception as e:
            _msg_failed = True
            logger.error(
                "Error processing WhatsApp message",
                extra_data={"message_id": message.message_id, "error": str(e)},
                exc_info=True,
            )
        finally:
            # סימון הודעה כ-completed רק אם העיבוד הצליח —
            # הודעה שנכשלה נשארת ב-processing ומאפשרת retry אחרי timeout
            if not _msg_failed and message.message_id:
                try:
                    await _mark_message_completed(db, message.message_id)
                except Exception:
                    logger.error(
                        "Failed to mark message as completed",
                        extra_data={"message_id": message.message_id},
                        exc_info=True,
                    )

    return {"processed": len(responses), "responses": responses}


@router.get(
    "/webhook",
    summary="Webhook Verification - WhatsApp",
    description="אימות webhook (challenge) עבור WhatsApp Business API.",
)
async def whatsapp_verify(
    hub_mode: str = None, hub_challenge: str = None, hub_verify_token: str = None
):
    """Webhook verification for WhatsApp Business API"""
    if hub_mode == "subscribe" and hub_challenge:
        return int(hub_challenge)
    return {"status": "ok"}
