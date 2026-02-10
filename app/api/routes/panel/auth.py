"""
אימות לפאנל ווב — כניסה באמצעות OTP
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    TokenPayload,
    create_access_token,
    generate_otp,
    store_otp,
    verify_otp,
)
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator
from app.db.database import get_db
from app.db.models.user import User, UserRole
from app.domain.services.station_service import StationService
from app.api.dependencies.auth import get_current_station_owner

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


class TokenResponse(BaseModel):
    """תגובת התחברות"""
    access_token: str
    token_type: str = "bearer"
    station_id: int
    station_name: str


class MeResponse(BaseModel):
    """פרטי המשתמש המחובר"""
    user_id: int
    station_id: int
    station_name: str
    role: str


# ==================== Endpoints ====================


@router.post(
    "/request-otp",
    summary="בקשת קוד כניסה",
    description="שולח קוד OTP לבעל התחנה. הקוד נשלח דרך הבוט (Telegram/WhatsApp).",
    responses={
        200: {"description": "OTP נשלח בהצלחה"},
        403: {"description": "המשתמש אינו בעל תחנה"},
        404: {"description": "משתמש לא נמצא"},
    },
    tags=["Panel - אימות"],
)
async def request_otp(
    data: OTPRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """בקשת קוד כניסה — שולח OTP דרך הבוט"""
    # חיפוש המשתמש
    result = await db.execute(
        select(User).where(User.phone_number == data.phone_number)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="משתמש לא נמצא עם מספר הטלפון הזה",
        )

    # ולידציה שהוא בעל תחנה
    if user.role != UserRole.STATION_OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="הגישה לפאנל מותרת לבעלי תחנות בלבד",
        )

    # ולידציה שיש לו תחנה פעילה
    station_service = StationService(db)
    station = await station_service.get_station_by_owner(user.id)
    if not station:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="לא נמצאה תחנה פעילה למשתמש",
        )

    # יצירת ושמירת OTP
    otp = generate_otp()
    await store_otp(user.id, otp)

    # שליחת OTP דרך הבוט
    # TODO: לשלוח הודעה ל-Telegram/WhatsApp עם הקוד
    # לפי user.platform — להשתמש ב-background_tasks
    logger.info(
        "OTP requested for panel login",
        extra_data={
            "user_id": user.id,
            "phone": PhoneNumberValidator.mask(data.phone_number),
            "station_id": station.id,
        },
    )

    return {"message": "קוד כניסה נשלח אליך דרך הבוט"}


@router.post(
    "/verify-otp",
    response_model=TokenResponse,
    summary="אימות קוד כניסה",
    description="אימות קוד OTP וקבלת JWT token לגישה לפאנל.",
    responses={
        200: {"description": "התחברות הצליחה"},
        401: {"description": "קוד שגוי או פג תוקף"},
        404: {"description": "משתמש לא נמצא"},
    },
    tags=["Panel - אימות"],
)
async def verify_otp_endpoint(
    data: OTPVerify,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """אימות OTP והנפקת JWT token"""
    # חיפוש המשתמש
    result = await db.execute(
        select(User).where(User.phone_number == data.phone_number)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="משתמש לא נמצא",
        )

    # אימות OTP
    is_valid = await verify_otp(user.id, data.otp)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="קוד שגוי או פג תוקף",
        )

    # קבלת תחנה
    station_service = StationService(db)
    station = await station_service.get_station_by_owner(user.id)
    if not station:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="לא נמצאה תחנה פעילה",
        )

    # הנפקת JWT
    token = create_access_token(
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
