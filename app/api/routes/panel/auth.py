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
)
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
    if not await try_set_otp_cooldown_by_phone(data.phone_number):
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
        f"הקוד שלך: <b>{escape(otp)}</b>\n\n"
        f"הקוד תקף ל-5 דקות.\n"
        f"אם לא ביקשת קוד — התעלם מהודעה זו."
    )

    platform_str = user.platform or "telegram"
    if platform_str == "telegram" and user.telegram_chat_id:
        platform = MessagePlatform.TELEGRAM
        recipient_id = user.telegram_chat_id
    else:
        platform = MessagePlatform.WHATSAPP
        recipient_id = user.phone_number

    outbox = OutboxService(db)
    await outbox.queue_message(
        platform=platform,
        recipient_id=recipient_id,
        message_type="panel_otp",
        message_content={"message_text": otp_message},
    )
    await db.commit()

    # שמירת OTP ב-Redis רק אחרי commit מוצלח — מבטיח שההודעה באמת תישלח
    await store_otp(user.id, otp)

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
