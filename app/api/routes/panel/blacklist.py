"""
רשימה שחורה — צפייה, הוספה, הוספה מרובה, הסרה
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.auth import TokenPayload
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.db.models.station_blacklist import StationBlacklist
from app.db.models.user import User
from app.domain.services.station_service import StationService
from app.api.routes.panel.schemas import ActionResponse, BulkResultItem

logger = get_logger(__name__)

router = APIRouter()


# ==================== סכמות ====================


class BlacklistItemResponse(BaseModel):
    """נהג חסום"""
    courier_id: int
    name: str
    phone_masked: str
    reason: str
    blocked_at: str


class AddToBlacklistRequest(BaseModel):
    """הוספה לרשימה שחורה"""
    phone_number: str
    reason: str = ""

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PhoneNumberValidator.validate(v):
            raise ValueError("מספר טלפון לא תקין")
        return PhoneNumberValidator.normalize(v)


class BulkBlacklistRequest(BaseModel):
    """הוספה מרובה לרשימה שחורה"""
    entries: List[AddToBlacklistRequest]

    @field_validator("entries")
    @classmethod
    def validate_entries(cls, v: list) -> list:
        if len(v) > 50:
            raise ValueError("מקסימום 50 רשומות בפעולה אחת")
        return v


class BulkBlacklistResponse(BaseModel):
    """תוצאות הוספה מרובה"""
    results: List[BulkResultItem]
    total: int
    success_count: int


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=List[BlacklistItemResponse],
    summary="רשימה שחורה",
    description="מחזיר את כל הנהגים החסומים בתחנה.",
    tags=["Panel - רשימה שחורה"],
)
async def list_blacklist(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> List[BlacklistItemResponse]:
    """רשימה שחורה עם פרטי משתמש"""
    result = await db.execute(
        select(StationBlacklist)
        .where(StationBlacklist.station_id == auth.station_id)
        .order_by(StationBlacklist.blocked_at.desc())
    )
    entries = list(result.scalars().all())

    # שליפת פרטי משתמשים
    if not entries:
        return []

    courier_ids = [e.courier_id for e in entries]
    users_result = await db.execute(
        select(User).where(User.id.in_(courier_ids))
    )
    users_by_id = {u.id: u for u in users_result.scalars().all()}

    return [
        BlacklistItemResponse(
            courier_id=e.courier_id,
            name=(users_by_id.get(e.courier_id, None) and (
                users_by_id[e.courier_id].name
                or users_by_id[e.courier_id].full_name
                or "לא צוין"
            )) or "לא צוין",
            phone_masked=(
                PhoneNumberValidator.mask(users_by_id[e.courier_id].phone_number)
                if e.courier_id in users_by_id and users_by_id[e.courier_id].phone_number
                else ""
            ),
            reason=e.reason or "",
            blocked_at=e.blocked_at.isoformat() if e.blocked_at else "",
        )
        for e in entries
    ]


@router.post(
    "",
    response_model=ActionResponse,
    summary="הוספה לרשימה שחורה",
    description="חסימת נהג בתחנה לפי מספר טלפון.",
    tags=["Panel - רשימה שחורה"],
)
async def add_to_blacklist(
    data: AddToBlacklistRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """הוספה לרשימה שחורה"""
    station_service = StationService(db)
    success, message = await station_service.add_to_blacklist(
        auth.station_id, data.phone_number, data.reason, actor_user_id=auth.user_id,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return ActionResponse(success=True, message=message)


@router.post(
    "/bulk",
    response_model=BulkBlacklistResponse,
    summary="הוספה מרובה לרשימה שחורה",
    description="חסימת כמה נהגים בפעולה אחת.",
    tags=["Panel - רשימה שחורה"],
)
async def add_to_blacklist_bulk(
    data: BulkBlacklistRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> BulkBlacklistResponse:
    """הוספה מרובה לרשימה שחורה"""
    station_service = StationService(db)
    results: list[BulkResultItem] = []
    success_count = 0

    for entry in data.entries:
        success, message = await station_service.add_to_blacklist(
            auth.station_id, entry.phone_number, entry.reason, actor_user_id=auth.user_id,
        )
        results.append(BulkResultItem(
            phone_masked=PhoneNumberValidator.mask(entry.phone_number),
            success=success,
            message=message,
        ))
        if success:
            success_count += 1

    return BulkBlacklistResponse(
        results=results,
        total=len(data.entries),
        success_count=success_count,
    )


@router.delete(
    "/{courier_id}",
    response_model=ActionResponse,
    summary="הסרה מרשימה שחורה",
    description="ביטול חסימת נהג בתחנה.",
    responses={400: {"description": "הנהג לא נמצא ברשימה"}},
    tags=["Panel - רשימה שחורה"],
)
async def remove_from_blacklist(
    courier_id: int,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """הסרה מרשימה שחורה"""
    station_service = StationService(db)
    success, message = await station_service.remove_from_blacklist(
        auth.station_id, courier_id, actor_user_id=auth.user_id,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return ActionResponse(success=True, message=message)
