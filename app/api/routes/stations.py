"""
Station API Routes - ניהול תחנות משלוחים

כל ה-endpoints דורשים אימות אדמין (X-Admin-API-Key).
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.admin_auth import require_admin_api_key
from app.db.database import get_db
from app.db.models.station import Station
from app.db.models.user import UserRole
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


class StationListResponse(BaseModel):
    """סכמת תגובה לרשימת תחנות"""
    stations: List[StationResponse]
    total: int


@router.get(
    "/",
    response_model=StationListResponse,
    summary="רשימת כל התחנות הפעילות",
    description="מחזיר את כל התחנות הפעילות במערכת. דורש מפתח אדמין.",
    responses={
        200: {"description": "רשימת תחנות"},
        401: {"description": "מפתח API חסר"},
        403: {"description": "מפתח API שגוי"},
    },
    tags=["Stations"],
)
async def list_stations(
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> StationListResponse:
    """רשימת כל התחנות הפעילות — לאדמין בלבד"""
    result = await db.execute(
        select(Station).where(
            Station.is_active == True  # noqa: E712
        ).order_by(Station.id)
    )
    stations = list(result.scalars().all())

    logger.info(
        "Admin listed stations",
        extra_data={"count": len(stations)},
    )

    return StationListResponse(
        stations=[StationResponse.model_validate(s) for s in stations],
        total=len(stations),
    )


@router.post(
    "/",
    response_model=StationResponse,
    summary="יצירת תחנה חדשה",
    description=(
        "יצירת תחנה חדשה והקצאת בעלים לפי מספר טלפון. "
        "המשתמש הופך אוטומטית ל-STATION_OWNER. דורש מפתח אדמין."
    ),
    responses={
        200: {"description": "התחנה נוצרה בהצלחה"},
        400: {"description": "למשתמש כבר יש תחנה פעילה"},
        401: {"description": "מפתח API חסר"},
        403: {"description": "מפתח API שגוי"},
        422: {"description": "שגיאת ולידציה"},
    },
    tags=["Stations"],
)
async def create_station(
    station_data: StationCreate,
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> StationResponse:
    """
    יצירת תחנה חדשה — דורש מפתח אדמין.

    - **name**: שם התחנה
    - **owner_phone**: מספר טלפון של בעל התחנה (אם לא קיים — ייווצר אוטומטית)
    """
    # חיפוש המשתמש לפי מספר טלפון — אם לא קיים, יוצרים אותו אוטומטית
    station_service = StationService(db)
    user = await station_service.get_or_create_user_by_phone(
        station_data.owner_phone, context="בעת יצירת תחנה",
    )

    # בדיקה שאין כבר תחנה פעילה לבעלים הזה
    existing = await station_service.get_station_by_owner(user.id)
    if existing:
        # תיקון תפקיד המשתמש אם נדרש (למקרה שהתחנה נוצרה אבל הרול לא עודכן)
        if user.role != UserRole.STATION_OWNER:
            user.role = UserRole.STATION_OWNER
            await db.commit()
            logger.info(
                "תוקן תפקיד המשתמש ל-STATION_OWNER עבור תחנה קיימת",
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
        "תחנה נוצרה ע\"י אדמין",
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
    description="מחזיר תחנה פעילה לפי מזהה. דורש מפתח אדמין.",
    responses={
        200: {"description": "התחנה נמצאה"},
        401: {"description": "מפתח API חסר"},
        403: {"description": "מפתח API שגוי"},
        404: {"description": "תחנה לא נמצאה"},
    },
    tags=["Stations"],
)
async def get_station(
    station_id: int,
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> StationResponse:
    """קבלת תחנה לפי ID — דורש מפתח אדמין"""
    station_service = StationService(db)
    station = await station_service.get_station(station_id)
    if not station:
        raise HTTPException(status_code=404, detail="תחנה לא נמצאה")
    return StationResponse.model_validate(station)
