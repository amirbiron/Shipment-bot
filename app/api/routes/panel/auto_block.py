"""
הגדרות חסימה אוטומטית — צפייה ועדכון הגדרות חסימה אוטומטית לתחנה

סעיף 10 מתוך Issue #210.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.logging import get_logger
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.domain.services.station_service import StationService
from app.api.routes.panel.schemas import ActionResponse

logger = get_logger(__name__)

router = APIRouter()


# ==================== סכמות ====================


class AutoBlockSettingsResponse(BaseModel):
    """הגדרות חסימה אוטומטית של תחנה"""
    auto_block_enabled: bool
    auto_block_grace_months: int
    auto_block_min_debt: float


class UpdateAutoBlockSettingsRequest(BaseModel):
    """עדכון הגדרות חסימה אוטומטית"""
    auto_block_enabled: bool | None = None
    auto_block_grace_months: int | None = None
    auto_block_min_debt: float | None = None

    @field_validator("auto_block_grace_months")
    @classmethod
    def validate_grace_months(cls, v: int | None) -> int | None:
        if v is not None and (v < 1 or v > 12):
            raise ValueError("תקופת חסד חייבת להיות בין 1 ל-12 חודשים")
        return v

    @field_validator("auto_block_min_debt")
    @classmethod
    def validate_min_debt(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("סף חוב מינימלי לא יכול להיות שלילי")
        return v


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=AutoBlockSettingsResponse,
    summary="הגדרות חסימה אוטומטית",
    description=(
        "מחזיר את הגדרות החסימה האוטומטית של התחנה: "
        "האם פעילה, תקופת חסד (חודשים), וסף חוב מינימלי."
    ),
    responses={
        200: {"description": "הגדרות חסימה אוטומטית"},
        401: {"description": "טוקן לא תקין"},
        404: {"description": "תחנה לא נמצאה"},
    },
    tags=["Panel - חסימה אוטומטית"],
)
async def get_auto_block_settings(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> AutoBlockSettingsResponse:
    """קבלת הגדרות חסימה אוטומטית של התחנה"""
    station_service = StationService(db)
    station = await station_service.get_station(auth.station_id)

    if not station:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="התחנה לא נמצאה",
        )

    return AutoBlockSettingsResponse(
        auto_block_enabled=station.auto_block_enabled,
        auto_block_grace_months=station.auto_block_grace_months,
        auto_block_min_debt=float(station.auto_block_min_debt),
    )


@router.put(
    "",
    response_model=ActionResponse,
    summary="עדכון הגדרות חסימה אוטומטית",
    description=(
        "עדכון הגדרות חסימה אוטומטית לתחנה. "
        "רק שדות שנשלחים מתעדכנים. "
        "תקופת חסד: 1-12 חודשים. סף חוב: 0 ומעלה."
    ),
    responses={
        200: {"description": "הגדרות עודכנו בהצלחה"},
        400: {"description": "שגיאת ולידציה"},
        401: {"description": "טוקן לא תקין"},
        404: {"description": "תחנה לא נמצאה"},
    },
    tags=["Panel - חסימה אוטומטית"],
)
async def update_auto_block_settings(
    data: UpdateAutoBlockSettingsRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """עדכון הגדרות חסימה אוטומטית של התחנה"""
    station_service = StationService(db)
    success, message = await station_service.update_auto_block_settings(
        station_id=auth.station_id,
        auto_block_enabled=data.auto_block_enabled,
        auto_block_grace_months=data.auto_block_grace_months,
        auto_block_min_debt=data.auto_block_min_debt,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    return ActionResponse(success=True, message=message)
