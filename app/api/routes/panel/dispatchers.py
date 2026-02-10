"""
ניהול סדרנים — הוספה, הסרה, רשימה, הוספה מרובה
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
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.user import User
from app.domain.services.station_service import StationService
from app.api.routes.panel.schemas import ActionResponse, BulkResultItem

logger = get_logger(__name__)

router = APIRouter()


# ==================== סכמות ====================


class DispatcherResponse(BaseModel):
    """סדרן בודד"""
    user_id: int
    name: str
    phone_masked: str
    is_active: bool
    created_at: str

    class Config:
        from_attributes = True


class AddDispatcherRequest(BaseModel):
    """הוספת סדרן"""
    phone_number: str

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PhoneNumberValidator.validate(v):
            raise ValueError("מספר טלפון לא תקין")
        return PhoneNumberValidator.normalize(v)


class BulkAddDispatchersRequest(BaseModel):
    """הוספת כמה סדרנים"""
    phone_numbers: List[str]

    @field_validator("phone_numbers")
    @classmethod
    def validate_and_normalize(cls, v: List[str]) -> List[str]:
        if len(v) > 50:
            raise ValueError("מקסימום 50 סדרנים בפעולה אחת")
        # strip + normalize — כפילויות נשמרות ומדווחות בתגובה
        result: list[str] = []
        for phone in v:
            stripped = phone.strip()
            if not stripped:
                continue
            if PhoneNumberValidator.validate(stripped):
                result.append(PhoneNumberValidator.normalize(stripped))
            else:
                result.append(stripped)
        return result


class BulkAddResponse(BaseModel):
    """תוצאות הוספה מרובה"""
    results: List[BulkResultItem]
    total: int
    success_count: int


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=List[DispatcherResponse],
    summary="רשימת סדרנים",
    description="מחזיר את כל הסדרנים הפעילים בתחנה.",
    tags=["Panel - סדרנים"],
)
async def list_dispatchers(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> List[DispatcherResponse]:
    """רשימת סדרנים עם פרטי משתמש"""
    result = await db.execute(
        select(StationDispatcher)
        .options(joinedload(StationDispatcher.user))
        .where(
            StationDispatcher.station_id == auth.station_id,
            StationDispatcher.is_active == True,  # noqa: E712
        )
        .order_by(StationDispatcher.created_at.desc())
    )
    dispatchers = list(result.scalars().unique().all())

    return [
        DispatcherResponse(
            user_id=d.user_id,
            name=d.user.name or d.user.full_name or "לא צוין",
            phone_masked=PhoneNumberValidator.mask(d.user.phone_number) if d.user.phone_number else "",
            is_active=d.is_active,
            created_at=d.created_at.isoformat() if d.created_at else "",
        )
        for d in dispatchers
    ]


@router.post(
    "",
    response_model=ActionResponse,
    summary="הוספת סדרן",
    description="הוספת סדרן לתחנה לפי מספר טלפון.",
    responses={
        200: {"description": "הסדרן נוסף בהצלחה"},
        422: {"description": "שגיאת ולידציה"},
    },
    tags=["Panel - סדרנים"],
)
async def add_dispatcher(
    data: AddDispatcherRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """הוספת סדרן — מבוסס על StationService.add_dispatcher"""
    station_service = StationService(db)
    success, message = await station_service.add_dispatcher(
        auth.station_id, data.phone_number,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return ActionResponse(success=True, message=message)


@router.post(
    "/bulk",
    response_model=BulkAddResponse,
    summary="הוספת סדרנים בכמות",
    description="הוספת כמה סדרנים בפעולה אחת. מחזיר תוצאה לכל מספר.",
    tags=["Panel - סדרנים"],
)
async def add_dispatchers_bulk(
    data: BulkAddDispatchersRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> BulkAddResponse:
    """הוספה מרובה — לולאה על add_dispatcher"""
    station_service = StationService(db)
    results: list[BulkResultItem] = []
    success_count = 0

    for phone in data.phone_numbers:
        if not PhoneNumberValidator.validate(phone):
            results.append(BulkResultItem(
                phone_masked=phone[:4] + "****",
                success=False,
                message="מספר טלפון לא תקין",
            ))
            continue

        normalized = PhoneNumberValidator.normalize(phone)
        success, message = await station_service.add_dispatcher(
            auth.station_id, normalized,
        )
        results.append(BulkResultItem(
            phone_masked=PhoneNumberValidator.mask(normalized),
            success=success,
            message=message,
        ))
        if success:
            success_count += 1

    return BulkAddResponse(
        results=results,
        total=len(data.phone_numbers),
        success_count=success_count,
    )


@router.delete(
    "/{user_id}",
    response_model=ActionResponse,
    summary="הסרת סדרן",
    description="הסרת סדרן מהתחנה.",
    responses={
        200: {"description": "הסדרן הוסר"},
        400: {"description": "הסדרן לא נמצא"},
    },
    tags=["Panel - סדרנים"],
)
async def remove_dispatcher(
    user_id: int,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """הסרת סדרן — מבוסס על StationService.remove_dispatcher"""
    station_service = StationService(db)
    success, message = await station_service.remove_dispatcher(
        auth.station_id, user_id,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return ActionResponse(success=True, message=message)
