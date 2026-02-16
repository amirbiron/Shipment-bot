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
    summary="Get auto-block settings",
    description=(
        "Returns the station's auto-blocking configuration: "
        "enabled flag, grace period (months), and minimum debt threshold."
    ),
    responses={
        200: {"description": "Auto-block settings returned successfully"},
        401: {"description": "Invalid or missing token"},
        404: {"description": "Station not found"},
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
    summary="Update auto-block settings",
    description=(
        "Update the station's auto-blocking configuration. "
        "Only provided fields are updated. "
        "Grace period: 1-12 months. Minimum debt: >= 0."
    ),
    responses={
        200: {"description": "Settings updated successfully"},
        400: {"description": "Validation error"},
        401: {"description": "Invalid or missing token"},
        404: {"description": "Station not found"},
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
        actor_user_id=auth.user_id,
    )

    if not success:
        # "התחנה לא נמצאה" → 404, שאר שגיאות ולידציה → 400
        status_code = (
            status.HTTP_404_NOT_FOUND
            if "לא נמצאה" in message
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=message)

    return ActionResponse(success=True, message=message)
