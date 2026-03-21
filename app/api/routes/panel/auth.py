"""
אימות לפאנל ווב — כניסה באמצעות OTP

זרימה:
1. בעל תחנה מבקש OTP → נשלח אליו דרך הבוט (Telegram/WhatsApp)
2. מזין את הקוד בפאנל → מקבל JWT token
"""
from html import escape
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    generate_otp,
    store_otp,
    try_set_otp_cooldown_by_phone,
    verify_otp,
    verify_refresh_token,
    verify_telegram_login_data,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator
from app.db.database import get_db
from app.db.models.outbox_message import MessagePlatform
from app.db.models.user import User, UserRole
from app.domain.services.outbox_service import OutboxService
from app.domain.services.station_service import StationService
from app.api.dependencies.auth import get_current_station_owner
from app.api.routes.panel.schemas import ActionResponse

logger = get_logger(__name__)

router = APIRouter()


# ==================== סכמות ====================


class OTPRequest(BaseModel):
    """בקשת OTP"""
    phone_number: str

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PhoneNumberValidator.validate(v):
            raise ValueError("מספר טלפון לא תקין")
        return PhoneNumberValidator.normalize(v)


class OTPVerify(BaseModel):
    """אימות OTP"""
    phone_number: str
    otp: str
    station_id: Optional[int] = None  # אופציונלי — אם יש כמה תחנות, המשתמש בוחר

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PhoneNumberValidator.validate(v):
            raise ValueError("מספר טלפון לא תקין")
        return PhoneNumberValidator.normalize(v)

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 6:
            raise ValueError("קוד OTP חייב להיות 6 ספרות")
        return v


class StationOption(BaseModel):
    """תחנה לבחירה"""
    station_id: int
    station_name: str


class TokenResponse(BaseModel):
    """תגובת התחברות"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    station_id: int
    station_name: str


class StationPickerResponse(BaseModel):
    """תגובה כשיש כמה תחנות — המשתמש צריך לבחור"""
    choose_station: bool = True
    stations: List[StationOption]


class RefreshRequest(BaseModel):
    """בקשת רענון טוקן"""
    refresh_token: str

    @field_validator("refresh_token")
    @classmethod
    def validate_refresh_token(cls, v: str) -> str:
        if not v or len(v) < 10:
            raise ValueError("refresh token לא תקין")
        return v


class TelegramLoginRequest(BaseModel):
    """נתוני אימות מ-Telegram Login Widget"""
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str

    @field_validator("auth_date")
    @classmethod
    def validate_auth_date(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("auth_date חייב להיות חיובי")
        return v


class TelegramBotInfoResponse(BaseModel):
    """מידע על הבוט — נדרש ל-Telegram Login Widget"""
    bot_username: str
    bot_id: str
    enabled: bool


class SwitchStationRequest(BaseModel):
    """בקשת מעבר בין תחנות"""
    station_id: int

    @field_validator("station_id")
    @classmethod
    def validate_station_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("מזהה תחנה חייב להיות מספר חיובי")
        return v


class MeResponse(BaseModel):
    """פרטי המשתמש המחובר"""
    user_id: int
    station_id: int
    station_name: str
    role: str


# ==================== Endpoints ====================


_OTP_GENERIC_RESPONSE = "אם המספר רשום במערכת ויש לו הרשאה, קוד כניסה יישלח בקרוב"


@router.post(
    "/request-otp",
    response_model=ActionResponse,
    summary="בקשת קוד כניסה",
    description="שולח קוד OTP לבעל התחנה. תשובה גנרית למניעת חשיפת מידע.",
    responses={
        200: {"description": "בקשה התקבלה"},
        429: {"description": "בקשת OTP מוקדמת מדי — נא להמתין"},
    },
    tags=["Panel - אימות"],
)
async def request_otp(
    data: OTPRequest,
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """בקשת קוד כניסה — תשובה גנרית למניעת user-enumeration"""
    # Rate limiting אטומי לפי טלפון — SET NX EX, לפני כל בדיקת קיום (מונע enumeration)
    try:
        cooldown_ok = await try_set_otp_cooldown_by_phone(data.phone_number)
    except Exception as e:
        logger.error(
            "כשלון בבדיקת cooldown ב-Redis — לא ניתן לשלוח OTP",
            extra_data={
                "phone": PhoneNumberValidator.mask(data.phone_number),
                "error": str(e),
            },
            exc_info=True,
        )
        return ActionResponse(
            success=False,
            message="אירעה שגיאה בשליחת קוד הכניסה, נסה שוב מאוחר יותר",
        )

    if not cooldown_ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="נא להמתין לפחות דקה בין בקשות קוד כניסה",
        )

    # חיפוש המשתמש
    result = await db.execute(
        select(User).where(User.phone_number == data.phone_number)
    )
    user = result.scalar_one_or_none()

    # תשובה גנרית אם המשתמש לא קיים / לא פעיל / לא בעל תחנה
    if not user:
        logger.info("OTP request for unknown phone", extra_data={
            "phone": PhoneNumberValidator.mask(data.phone_number),
        })
        return ActionResponse(success=True, message=_OTP_GENERIC_RESPONSE)

    if not user.is_active:
        logger.info("OTP request for inactive user", extra_data={
            "user_id": user.id,
        })
        return ActionResponse(success=True, message=_OTP_GENERIC_RESPONSE)

    if user.role != UserRole.STATION_OWNER:
        logger.info("OTP request for non-owner", extra_data={
            "user_id": user.id, "role": str(user.role),
        })
        return ActionResponse(success=True, message=_OTP_GENERIC_RESPONSE)

    # ולידציה שיש לו תחנה פעילה (בודק גם station_owners וגם owner_id ישן)
    station_service = StationService(db)
    stations = await station_service.get_stations_by_owner(user.id)
    if not stations:
        logger.info("OTP request for owner without station", extra_data={
            "user_id": user.id,
        })
        return ActionResponse(success=True, message=_OTP_GENERIC_RESPONSE)

    # יצירת OTP
    otp = generate_otp()

    # שליחת OTP דרך הבוט — לפי הפלטפורמה של המשתמש
    otp_message = (
        f"🔐 <b>קוד כניסה לפאנל</b>\n\n"
        f"קוד אימות OTP שלך: <b>{escape(otp)}</b>\n\n"
        f"הקוד תקף ל-5 דקות.\n"
        f"אם לא ביקשת קוד — התעלם מהודעה זו."
    )

    platform_str = user.platform or "telegram"
    if platform_str == "telegram" and user.telegram_chat_id:
        platform = MessagePlatform.TELEGRAM
        recipient_id = user.telegram_chat_id
    elif user.phone_number:
        platform = MessagePlatform.WHATSAPP
        recipient_id = user.phone_number
        if platform_str == "telegram":
            logger.warning(
                "שליחת OTP דרך WhatsApp כי אין telegram_chat_id — "
                "המשתמש רשום כטלגרם אבל אף פעם לא פתח את הבוט",
                extra_data={
                    "user_id": user.id,
                    "phone": PhoneNumberValidator.mask(user.phone_number),
                },
            )
    else:
        logger.error(
            "אין אמצעי שליחה עבור OTP — אין telegram_chat_id ואין phone_number",
            extra_data={"user_id": user.id},
        )
        return ActionResponse(success=True, message=_OTP_GENERIC_RESPONSE)

    # שמירת OTP ב-Redis לפני commit של ההודעה — מבטיח שהקוד ניתן לאימות
    # לפני שההודעה קיימת ב-outbox. אם Redis כושל, לא עושים commit ולא שולחים כלום.
    try:
        await store_otp(user.id, otp)
    except Exception as e:
        logger.error(
            "כשלון בשמירת OTP ב-Redis — לא שולחים הודעה",
            extra_data={
                "user_id": user.id,
                "error": str(e),
            },
            exc_info=True,
        )
        return ActionResponse(
            success=False,
            message="אירעה שגיאה בשליחת קוד הכניסה, נסה שוב בעוד דקה",
        )

    outbox = OutboxService(db)
    msg = await outbox.queue_message(
        platform=platform,
        recipient_id=recipient_id,
        message_type="panel_otp",
        message_content={"message_text": otp_message},
    )
    await db.commit()

    # שליחה מיידית — לא מחכים ל-beat scheduler (10 שניות + backlog)
    # אם Celery broker לא זמין, ההודעה תישלח דרך ה-beat הרגיל
    try:
        from app.workers.tasks import send_message
        send_message.delay(msg.id)
    except Exception as e:
        logger.warning(
            "לא ניתן לשלוח OTP מיידית דרך Celery — ייאסף ב-beat הבא",
            extra_data={"message_id": msg.id, "error": str(e)},
        )

    logger.info(
        "OTP requested for panel login",
        extra_data={
            "user_id": user.id,
            "phone": PhoneNumberValidator.mask(data.phone_number),
            "station_ids": [s.id for s in stations],
            "platform": platform_str,
        },
    )

    return ActionResponse(success=True, message=_OTP_GENERIC_RESPONSE)


@router.post(
    "/verify-otp",
    response_model=Union[TokenResponse, StationPickerResponse],
    summary="אימות קוד כניסה",
    description=(
        "אימות קוד OTP וקבלת JWT token. "
        "אם למשתמש יש כמה תחנות, מחזיר רשימה לבחירה (יש לשלוח שוב עם station_id)."
    ),
    responses={
        200: {"description": "התחברות הצליחה או בחירת תחנה"},
        401: {"description": "קוד שגוי, פג תוקף, או משתמש לא זוהה"},
    },
    tags=["Panel - אימות"],
)
async def verify_otp_endpoint(
    data: OTPVerify,
    db: AsyncSession = Depends(get_db),
) -> Union[TokenResponse, StationPickerResponse]:
    """אימות OTP והנפקת JWT token — עם תמיכה בריבוי תחנות"""
    # חיפוש המשתמש
    result = await db.execute(
        select(User).where(User.phone_number == data.phone_number)
    )
    user = result.scalar_one_or_none()

    # תשובה אחידה לכל כשלון — מונע user-enumeration
    if not user or not user.is_active or user.role != UserRole.STATION_OWNER:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="קוד שגוי או פג תוקף",
        )

    # קבלת תחנות — לפני צריכת OTP, כדי לדעת אם צריך station picker
    # תשובה אחידה (401) גם כשאין תחנות — מונע user-enumeration
    station_service = StationService(db)
    stations = await station_service.get_stations_by_owner(user.id)
    if not stations:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="קוד שגוי או פג תוקף",
        )

    # אם יש כמה תחנות והמשתמש לא בחר — מאמתים בלי לצרוך את ה-OTP
    need_station_picker = len(stations) > 1 and data.station_id is None

    # אימות OTP (כולל בדיקת מגבלת ניסיונות)
    # consume=False כשצריך station picker — ה-OTP נשאר תקף לקריאה הבאה עם station_id
    is_valid = await verify_otp(user.id, data.otp, consume=not need_station_picker)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="קוד שגוי או פג תוקף",
        )

    # אם יש כמה תחנות והמשתמש לא בחר — מחזיר רשימה לבחירה
    if need_station_picker:
        return StationPickerResponse(
            stations=[
                StationOption(station_id=s.id, station_name=s.name)
                for s in stations
            ],
        )

    # בחירת תחנה — אם צוין station_id מוודאים שהמשתמש באמת בעלים שלה
    if data.station_id is not None:
        station = next((s for s in stations if s.id == data.station_id), None)
        if not station:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="אין הרשאה לתחנה שנבחרה",
            )
    else:
        station = stations[0]

    # הנפקת JWT + refresh token
    token = create_access_token(
        user_id=user.id,
        station_id=station.id,
        role=user.role.value,
    )
    refresh = await create_refresh_token(
        user_id=user.id,
        station_id=station.id,
        role=user.role.value,
    )

    logger.info(
        "Panel login successful",
        extra_data={"user_id": user.id, "station_id": station.id},
    )

    return TokenResponse(
        access_token=token,
        refresh_token=refresh,
        station_id=station.id,
        station_name=station.name,
    )


@router.get(
    "/telegram-bot-info",
    response_model=TelegramBotInfoResponse,
    summary="מידע על הבוט לטלגרם",
    description="מחזיר את שם הבוט הנדרש ל-Telegram Login Widget.",
    responses={
        200: {"description": "מידע על הבוט"},
    },
    tags=["Panel - אימות"],
)
async def telegram_bot_info() -> TelegramBotInfoResponse:
    """מידע על הבוט — הפרונט צריך את ה-bot_id המספרי כדי לפתוח Telegram Login Widget"""
    bot_username = settings.TELEGRAM_BOT_USERNAME
    bot_token = settings.TELEGRAM_BOT_TOKEN
    # bot_id הוא החלק המספרי לפני ה-':' בטוקן
    bot_id = bot_token.split(":")[0] if bot_token and ":" in bot_token else ""
    # bot_id (מהטוקן) מספיק להפעלת הווידג'ט — bot_username אופציונלי
    enabled = bool(bot_id)
    return TelegramBotInfoResponse(
        bot_username=bot_username or "",
        bot_id=bot_id,
        enabled=enabled,
    )


@router.post(
    "/telegram-login",
    response_model=Union[TokenResponse, StationPickerResponse],
    summary="כניסה דרך Telegram Login Widget",
    description=(
        "אימות באמצעות Telegram Login Widget. "
        "הנתונים מאומתים מול הטוקן של הבוט (HMAC-SHA256). "
        "אם למשתמש יש כמה תחנות, מחזיר רשימה לבחירה."
    ),
    responses={
        200: {"description": "התחברות הצליחה או בחירת תחנה"},
        401: {"description": "אימות טלגרם נכשל או המשתמש לא מורשה"},
    },
    tags=["Panel - אימות"],
)
async def telegram_login(
    data: TelegramLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> Union[TokenResponse, StationPickerResponse]:
    """כניסה דרך Telegram Login Widget — אימות HMAC-SHA256 + הנפקת JWT"""
    # אימות נתוני טלגרם (hash + תוקף)
    # exclude_none — שדות None לא נשלחים מטלגרם ולא נכללים בחישוב ה-hash
    auth_data = data.model_dump(exclude_none=True)
    if not verify_telegram_login_data(auth_data):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="אימות טלגרם נכשל — נתונים לא תקינים או פגי תוקף",
        )

    # חיפוש משתמש לפי telegram_chat_id
    telegram_id = str(data.id)
    result = await db.execute(
        select(User).where(User.telegram_chat_id == telegram_id)
    )
    user = result.scalar_one_or_none()

    # תשובה אחידה לכל כשלון — מונע user-enumeration
    if not user or not user.is_active or user.role != UserRole.STATION_OWNER:
        logger.info("Telegram Login — משתמש לא מורשה", extra_data={
            "telegram_id": telegram_id,
            "user_found": user is not None,
            "is_active": user.is_active if user else None,
            "role": str(user.role) if user else None,
        })
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="המשתמש לא רשום כבעל תחנה או שהחשבון אינו פעיל",
        )

    # קבלת תחנות
    station_service = StationService(db)
    stations = await station_service.get_stations_by_owner(user.id)
    if not stations:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="המשתמש לא רשום כבעל תחנה או שהחשבון אינו פעיל",
        )

    # אם יש כמה תחנות — מחזיר בורר (בלי צריכת OTP כי אין כאן OTP)
    if len(stations) > 1:
        return StationPickerResponse(
            stations=[
                StationOption(station_id=s.id, station_name=s.name)
                for s in stations
            ],
        )

    station = stations[0]

    # הנפקת JWT + refresh token
    token = create_access_token(
        user_id=user.id,
        station_id=station.id,
        role=user.role.value,
    )
    refresh = await create_refresh_token(
        user_id=user.id,
        station_id=station.id,
        role=user.role.value,
    )

    logger.info(
        "Telegram Login — כניסה הצליחה",
        extra_data={"user_id": user.id, "station_id": station.id, "telegram_id": telegram_id},
    )

    return TokenResponse(
        access_token=token,
        refresh_token=refresh,
        station_id=station.id,
        station_name=station.name,
    )


@router.post(
    "/telegram-login-select-station",
    response_model=TokenResponse,
    summary="בחירת תחנה אחרי כניסה דרך טלגרם",
    description=(
        "כשלמשתמש יש כמה תחנות ונכנס דרך טלגרם — שולח את נתוני הטלגרם שוב עם בחירת תחנה."
    ),
    responses={
        200: {"description": "התחברות הצליחה"},
        401: {"description": "אימות טלגרם נכשל"},
        403: {"description": "אין הרשאה לתחנה שנבחרה"},
    },
    tags=["Panel - אימות"],
)
async def telegram_login_select_station(
    data: TelegramLoginRequest,
    station_id: int,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """בחירת תחנה אחרי Telegram Login — מאמת שוב את הנתונים ומנפיק JWT"""
    # אימות מחדש של נתוני טלגרם
    auth_data = data.model_dump(exclude_none=True)
    if not verify_telegram_login_data(auth_data):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="אימות טלגרם נכשל — נתונים לא תקינים או פגי תוקף",
        )

    # חיפוש משתמש
    telegram_id = str(data.id)
    result = await db.execute(
        select(User).where(User.telegram_chat_id == telegram_id)
    )
    user = result.scalar_one_or_none()

    if not user or not user.is_active or user.role != UserRole.STATION_OWNER:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="המשתמש לא רשום כבעל תחנה או שהחשבון אינו פעיל",
        )

    # ולידציה שהמשתמש בעלים של התחנה שנבחרה
    station_service = StationService(db)
    stations = await station_service.get_stations_by_owner(user.id)
    station = next((s for s in stations if s.id == station_id), None)
    if not station:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="אין הרשאה לתחנה שנבחרה",
        )

    # הנפקת JWT + refresh token
    token = create_access_token(
        user_id=user.id,
        station_id=station.id,
        role=user.role.value,
    )
    refresh = await create_refresh_token(
        user_id=user.id,
        station_id=station.id,
        role=user.role.value,
    )

    logger.info(
        "Telegram Login — בחירת תחנה הצליחה",
        extra_data={"user_id": user.id, "station_id": station.id, "telegram_id": telegram_id},
    )

    return TokenResponse(
        access_token=token,
        refresh_token=refresh,
        station_id=station.id,
        station_name=station.name,
    )


@router.get(
    "/me",
    response_model=MeResponse,
    summary="פרטי המשתמש המחובר",
    description="מחזיר פרטי המשתמש והתחנה של הטוקן הנוכחי.",
    responses={
        200: {"description": "פרטי משתמש"},
        401: {"description": "טוקן לא תקין"},
    },
    tags=["Panel - אימות"],
)
async def get_me(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    """פרטי המשתמש המחובר"""
    station_service = StationService(db)
    station = await station_service.get_station(auth.station_id)

    return MeResponse(
        user_id=auth.user_id,
        station_id=auth.station_id,
        station_name=station.name if station else "",
        role=auth.role,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="רענון טוקן",
    description=(
        "שליחת refresh token לקבלת access token חדש + refresh token חדש. "
        "ה-refresh token הישן נמחק (rotation) — כל טוקן חד-פעמי."
    ),
    responses={
        200: {"description": "טוקנים חדשים הונפקו"},
        401: {"description": "refresh token לא תקין או פג תוקף"},
        403: {"description": "המשתמש/תחנה לא פעילים"},
    },
    tags=["Panel - אימות"],
)
async def refresh_access_token(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """רענון טוקן — מנפיק access + refresh חדשים עם ולידציה מלאה"""
    # אימות refresh token (מוחק אותו מ-Redis — rotation)
    token_data = await verify_refresh_token(data.refresh_token)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token לא תקין או פג תוקף",
        )

    # ולידציה שהמשתמש עדיין פעיל ובעל תחנה
    user_result = await db.execute(
        select(User).where(User.id == token_data.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active or user.role != UserRole.STATION_OWNER:
        logger.warning(
            "Refresh rejected — user invalid",
            extra_data={"user_id": token_data.user_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="חשבון המשתמש אינו פעיל או שאינו בעל תחנה",
        )

    # ולידציה שהתחנה עדיין פעילה והמשתמש עדיין בעלים
    station_service = StationService(db)
    station = await station_service.get_station(token_data.station_id)
    if not station:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="התחנה לא פעילה",
        )

    is_owner = await station_service.is_owner_of_station(
        token_data.user_id, token_data.station_id
    )
    if not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="אין הרשאה — הבעלות על התחנה השתנתה",
        )

    # הנפקת טוקנים חדשים
    new_access = create_access_token(
        user_id=user.id,
        station_id=station.id,
        role=user.role.value,
    )
    new_refresh = await create_refresh_token(
        user_id=user.id,
        station_id=station.id,
        role=user.role.value,
    )

    logger.info(
        "Token refreshed",
        extra_data={"user_id": user.id, "station_id": station.id},
    )

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        station_id=station.id,
        station_name=station.name,
    )


@router.post(
    "/switch-station",
    response_model=TokenResponse,
    summary="מעבר מהיר בין תחנות",
    description=(
        "מעבר לתחנה אחרת שבבעלות המשתמש ללא צורך ב-OTP חדש. "
        "מנפיק JWT ו-refresh token חדשים עבור התחנה המבוקשת."
    ),
    responses={
        200: {"description": "טוקנים חדשים לתחנה המבוקשת"},
        403: {"description": "אין הרשאה לתחנה המבוקשת"},
    },
    tags=["Panel - אימות"],
)
async def switch_station(
    data: SwitchStationRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """מעבר מהיר בין תחנות — מנפיק טוקנים חדשים לתחנה המבוקשת"""
    station_service = StationService(db)

    # ולידציה: המשתמש הוא בעלים של התחנה המבוקשת
    is_owner = await station_service.is_owner_of_station(
        auth.user_id, data.station_id
    )
    if not is_owner:
        logger.warning(
            "ניסיון מעבר לתחנה ללא הרשאה",
            extra_data={
                "user_id": auth.user_id,
                "requested_station_id": data.station_id,
                "current_station_id": auth.station_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="אין הרשאה לתחנה המבוקשת",
        )

    # ולידציה: התחנה פעילה
    station = await station_service.get_station(data.station_id)
    if not station:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="התחנה לא פעילה",
        )

    # הנפקת טוקנים חדשים
    new_access = create_access_token(
        user_id=auth.user_id,
        station_id=station.id,
        role=auth.role,
    )
    new_refresh = await create_refresh_token(
        user_id=auth.user_id,
        station_id=station.id,
        role=auth.role,
    )

    logger.info(
        "מעבר בין תחנות",
        extra_data={
            "user_id": auth.user_id,
            "from_station_id": auth.station_id,
            "to_station_id": station.id,
        },
    )

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        station_id=station.id,
        station_name=station.name,
    )
