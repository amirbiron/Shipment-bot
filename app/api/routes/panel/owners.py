"""
ניהול בעלי תחנה — הוספה, הסרה, רשימה
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.auth import TokenPayload
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.db.models.station_owner import StationOwner
from app.db.models.user import User
from app.domain.services.station_service import StationService
from app.api.routes.panel.schemas import ActionResponse

logger = get_logger(__name__)

router = APIRouter()


# ==================== סכמות ====================


class OwnerResponse(BaseModel):
    """בעלים בודד"""
    user_id: int
    name: str
    phone_masked: str
    is_active: bool
    created_at: str

    class Config:
        from_attributes = True


class AddOwnerRequest(BaseModel):
    """הוספת בעלים"""
    phone_number: str

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PhoneNumberValidator.validate(v):
            raise ValueError("מספר טלפון לא תקין")
        return PhoneNumberValidator.normalize(v)


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=List[OwnerResponse],
    summary="רשימת בעלים",
    description="מחזיר את כל הבעלים הפעילים בתחנה.",
    tags=["Panel - בעלים"],
)
async def list_owners(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> List[OwnerResponse]:
    """רשימת בעלים עם פרטי משתמש"""
    result = await db.execute(
        select(StationOwner)
        .options(joinedload(StationOwner.user))
        .where(
            StationOwner.station_id == auth.station_id,
            StationOwner.is_active == True,  # noqa: E712
        )
        .order_by(StationOwner.created_at.asc())
    )
    owners = list(result.scalars().unique().all())

    return [
        OwnerResponse(
            user_id=o.user_id,
            name=o.user.name or o.user.full_name or "לא צוין",
            phone_masked=PhoneNumberValidator.mask(o.user.phone_number) if o.user.phone_number else "",
            is_active=o.is_active,
            created_at=o.created_at.isoformat() if o.created_at else "",
        )
        for o in owners
    ]


@router.post(
    "",
    response_model=ActionResponse,
    summary="הוספת בעלים",
    description="הוספת בעלים לתחנה לפי מספר טלפון.",
    responses={
        200: {"description": "הבעלים נוסף בהצלחה"},
        400: {"description": "שגיאה בהוספה"},
    },
    tags=["Panel - בעלים"],
)
async def add_owner(
    data: AddOwnerRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """הוספת בעלים — מבוסס על StationService.add_owner"""
    station_service = StationService(db)
    success, message = await station_service.add_owner(
        auth.station_id, data.phone_number,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return ActionResponse(success=True, message=message)


@router.delete(
    "/{user_id}",
    response_model=ActionResponse,
    summary="הסרת בעלים",
    description="הסרת בעלים מהתחנה. לא ניתן להסיר את הבעלים האחרון.",
    responses={
        200: {"description": "הבעלים הוסר"},
        400: {"description": "לא ניתן להסיר — בעלים אחרון או לא נמצא"},
    },
    tags=["Panel - בעלים"],
)
async def remove_owner(
    user_id: int,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """הסרת בעלים — לא ניתן להסיר את האחרון"""
    station_service = StationService(db)
    success, message = await station_service.remove_owner(
        auth.station_id, user_id,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return ActionResponse(success=True, message=message)
