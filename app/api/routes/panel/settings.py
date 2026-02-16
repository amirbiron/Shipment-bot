"""
הגדרות תחנה מורחבות — עריכת שם, תיאור, שעות פעילות, אזורי שירות, לוגו

סעיף 8 מתוך Issue #210.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.validation import OperatingHoursValidator
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.domain.services.station_service import StationService
from app.api.routes.panel.schemas import ActionResponse

router = APIRouter()


# ==================== סכמות ====================


class OperatingHoursDay(BaseModel):
    """שעות פעילות ליום בודד"""
    open: str
    close: str

    @field_validator("open", "close")
    @classmethod
    def validate_time(cls, v: str) -> str:
        """ולידציה של פורמט שעה HH:MM"""
        if not OperatingHoursValidator.TIME_PATTERN.match(v):
            raise ValueError("פורמט שעה לא תקין — יש להשתמש ב-HH:MM (00:00–23:59)")
        return v


class StationSettingsResponse(BaseModel):
    """הגדרות תחנה מורחבות"""
    name: str
    description: Optional[str] = None
    operating_hours: Optional[dict] = None
    service_areas: Optional[list[str]] = None
    logo_url: Optional[str] = None


class UpdateStationSettingsRequest(BaseModel):
    """עדכון הגדרות תחנה מורחבות — שדות אופציונליים, רק מה שנשלח מתעדכן.

    ולידציית מבנה בלבד — סניטציה ובדיקות injection מתבצעות בשכבת השירות.
    """
    name: Optional[str] = None
    description: Optional[str] = None
    operating_hours: Optional[dict[str, Optional[OperatingHoursDay]]] = None
    service_areas: Optional[list[str]] = None
    logo_url: Optional[str] = None
    # דגלים למחיקת שדות (איפוס ל-null)
    clear_description: bool = False
    clear_operating_hours: bool = False
    clear_service_areas: bool = False
    clear_logo_url: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        """ולידציית אורך מינימלי בלבד — סניטציה בשירות"""
        if v is not None:
            v = v.strip()
            if len(v) < 2:
                raise ValueError("שם התחנה חייב להכיל לפחות 2 תווים")
        return v


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=StationSettingsResponse,
    summary="הגדרות תחנה מורחבות",
    description="מחזיר את ההגדרות המורחבות של התחנה: שם, תיאור, שעות פעילות, אזורי שירות ולוגו.",
    responses={
        200: {"description": "הגדרות התחנה"},
        401: {"description": "טוקן לא תקין"},
        404: {"description": "תחנה לא נמצאה"},
    },
    tags=["Panel - הגדרות"],
)
async def get_station_settings(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> StationSettingsResponse:
    """קבלת הגדרות מורחבות של התחנה"""
    station_service = StationService(db)
    settings = await station_service.get_station_settings(auth.station_id)

    if not settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="התחנה לא נמצאה",
        )

    return StationSettingsResponse(
        name=settings.get("name", ""),
        description=settings.get("description"),
        operating_hours=settings.get("operating_hours"),
        service_areas=settings.get("service_areas"),
        logo_url=settings.get("logo_url"),
    )


@router.put(
    "",
    response_model=ActionResponse,
    summary="עדכון הגדרות תחנה מורחבות",
    description=(
        "עדכון הגדרות התחנה — שם, תיאור, שעות פעילות, אזורי שירות ולוגו. "
        "רק שדות שנשלחים מתעדכנים. להגדרת שדה ל-null יש להשתמש בדגלי clear_*."
    ),
    responses={
        200: {"description": "הגדרות עודכנו בהצלחה"},
        400: {"description": "שגיאת ולידציה"},
        401: {"description": "טוקן לא תקין"},
    },
    tags=["Panel - הגדרות"],
)
async def update_station_settings(
    data: UpdateStationSettingsRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """עדכון הגדרות מורחבות של התחנה"""
    station_service = StationService(db)

    # המרת operating_hours ל-dict רגיל (בלי Pydantic models)
    hours_value: dict | None = ...
    if data.clear_operating_hours:
        hours_value = None
    elif data.operating_hours is not None:
        hours_value = {}
        for day, schedule in data.operating_hours.items():
            hours_value[day] = (
                {"open": schedule.open, "close": schedule.close}
                if schedule is not None else None
            )

    # המרת service_areas
    areas_value: list | None = ...
    if data.clear_service_areas:
        areas_value = None
    elif data.service_areas is not None:
        areas_value = data.service_areas

    success, message = await station_service.update_station_settings(
        station_id=auth.station_id,
        name=data.name,
        description=None if data.clear_description else (data.description if data.description is not None else ...),
        operating_hours=hours_value,
        service_areas=areas_value,
        logo_url=None if data.clear_logo_url else (data.logo_url if data.logo_url is not None else ...),
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    return ActionResponse(success=success, message=message)
