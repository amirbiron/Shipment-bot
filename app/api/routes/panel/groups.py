"""
הגדרות קבוצות — צפייה ועדכון קבוצות תחנה
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.validation import TextSanitizer
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.domain.services.station_service import StationService
from app.api.routes.panel.schemas import ActionResponse

router = APIRouter()


# ==================== סכמות ====================


class GroupSettingsResponse(BaseModel):
    """הגדרות קבוצות נוכחיות"""
    public_group_chat_id: Optional[str] = None
    public_group_platform: Optional[str] = None
    private_group_chat_id: Optional[str] = None
    private_group_platform: Optional[str] = None


class UpdateGroupSettingsRequest(BaseModel):
    """עדכון הגדרות קבוצות"""
    public_group_chat_id: Optional[str] = None
    public_group_platform: Optional[str] = None
    private_group_chat_id: Optional[str] = None
    private_group_platform: Optional[str] = None

    @field_validator("public_group_platform", "private_group_platform")
    @classmethod
    def validate_platform(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("telegram", "whatsapp"):
            raise ValueError("פלטפורמה חייבת להיות telegram או whatsapp")
        return v

    @field_validator("public_group_chat_id", "private_group_chat_id")
    @classmethod
    def sanitize_chat_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return TextSanitizer.sanitize(v.strip(), max_length=100)
        return v


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=GroupSettingsResponse,
    summary="הגדרות קבוצות",
    description="מחזיר הגדרות קבוצות נוכחיות של התחנה.",
    tags=["Panel - קבוצות"],
)
async def get_group_settings(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> GroupSettingsResponse:
    """הגדרות קבוצות נוכחיות"""
    station_service = StationService(db)
    station = await station_service.get_station(auth.station_id)

    return GroupSettingsResponse(
        public_group_chat_id=station.public_group_chat_id if station else None,
        public_group_platform=station.public_group_platform if station else None,
        private_group_chat_id=station.private_group_chat_id if station else None,
        private_group_platform=station.private_group_platform if station else None,
    )


@router.put(
    "",
    response_model=ActionResponse,
    summary="עדכון הגדרות קבוצות",
    description="עדכון מזהי קבוצות התחנה (ציבורית/פרטית).",
    tags=["Panel - קבוצות"],
)
async def update_group_settings(
    data: UpdateGroupSettingsRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """עדכון הגדרות קבוצות — מבוסס על StationService.update_station_groups"""
    station_service = StationService(db)
    success, message = await station_service.update_station_groups(
        station_id=auth.station_id,
        public_group_chat_id=data.public_group_chat_id,
        public_group_platform=data.public_group_platform,
        private_group_chat_id=data.private_group_chat_id,
        private_group_platform=data.private_group_platform,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )
    return ActionResponse(success=success, message=message)
