"""
Station API Routes - ניהול תחנות משלוחים
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User, UserRole
from app.db.models.station import Station
from app.domain.services.station_service import StationService
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator, TextSanitizer

logger = get_logger(__name__)

router = APIRouter()


class StationCreate(BaseModel):
    """סכמה ליצירת תחנה חדשה"""
    name: str
    owner_phone: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("שם התחנה חייב להכיל לפחות 2 תווים")
        return TextSanitizer.sanitize(v, max_length=200)

    @field_validator("owner_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PhoneNumberValidator.validate(v):
            raise ValueError("מספר טלפון לא תקין")
        return PhoneNumberValidator.normalize(v)


class StationResponse(BaseModel):
    """סכמת תגובה לתחנה"""
    id: int
    name: str
    owner_id: int
    is_active: bool

    class Config:
        from_attributes = True


@router.post(
    "/",
    response_model=StationResponse,
    summary="יצירת תחנה חדשה",
    description=(
        "יצירת תחנה חדשה והקצאת בעלים לפי מספר טלפון. "
        "המשתמש הופך אוטומטית ל-STATION_OWNER."
    ),
    responses={
        200: {"description": "התחנה נוצרה בהצלחה"},
        404: {"description": "המשתמש לא נמצא"},
        400: {"description": "למשתמש כבר יש תחנה פעילה"},
        422: {"description": "שגיאת ולידציה"},
    },
    tags=["Stations"],
)
async def create_station(
    station_data: StationCreate,
    db: AsyncSession = Depends(get_db),
) -> StationResponse:
    """
    יצירת תחנה חדשה.

    - **name**: שם התחנה
    - **owner_phone**: מספר טלפון של בעל התחנה (חייב להיות משתמש קיים)
    """
    # חיפוש המשתמש לפי מספר טלפון — אם לא קיים, יוצרים אותו אוטומטית
    result = await db.execute(
        select(User).where(User.phone_number == station_data.owner_phone)
    )
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            phone_number=station_data.owner_phone,
            platform="telegram",
            role=UserRole.SENDER,
        )
        db.add(user)
        await db.flush()
        logger.info(
            "יצירת משתמש אוטומטית בעת יצירת תחנה",
            extra_data={
                "user_id": user.id,
                "phone": PhoneNumberValidator.mask(station_data.owner_phone),
            }
        )

    # בדיקה שאין כבר תחנה פעילה לבעלים הזה
    station_service = StationService(db)
    existing = await station_service.get_station_by_owner(user.id)
    if existing:
        # תיקון תפקיד המשתמש אם נדרש (למקרה שהתחנה נוצרה אבל הרול לא עודכן)
        if user.role != UserRole.STATION_OWNER:
            user.role = UserRole.STATION_OWNER
            await db.commit()
            logger.info(
                "Fixed user role to STATION_OWNER for existing station",
                extra_data={
                    "station_id": existing.id,
                    "owner_id": user.id,
                    "phone": PhoneNumberValidator.mask(station_data.owner_phone),
                }
            )
        raise HTTPException(status_code=400, detail="למשתמש כבר יש תחנה פעילה")

    # יצירת התחנה + עדכון תפקיד באותה טרנזקציה
    station = await station_service.create_station(
        name=station_data.name,
        owner_id=user.id,
    )

    if user.role != UserRole.STATION_OWNER:
        user.role = UserRole.STATION_OWNER

    await db.commit()
    await db.refresh(station)

    logger.info(
        "Station created via API",
        extra_data={
            "station_id": station.id,
            "owner_id": user.id,
            "phone": PhoneNumberValidator.mask(station_data.owner_phone),
        }
    )
    return StationResponse.model_validate(station)


@router.get(
    "/{station_id}",
    response_model=StationResponse,
    summary="קבלת תחנה לפי מזהה",
    description="מחזיר תחנה פעילה לפי מזהה.",
    responses={200: {"description": "התחנה נמצאה"}, 404: {"description": "תחנה לא נמצאה"}},
    tags=["Stations"],
)
async def get_station(
    station_id: int,
    db: AsyncSession = Depends(get_db),
) -> StationResponse:
    """קבלת תחנה לפי ID"""
    station_service = StationService(db)
    station = await station_service.get_station(station_id)
    if not station:
        raise HTTPException(status_code=404, detail="תחנה לא נמצאה")
    return StationResponse.model_validate(station)
