"""
ארנק תחנה — יתרה, היסטוריית תנועות עם pagination וסינון, עדכון אחוז עמלה
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType
from app.domain.services.station_service import StationService
from app.api.routes.panel.schemas import ActionResponse, parse_date_param
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ==================== סכמות ====================


class WalletResponse(BaseModel):
    """פרטי ארנק"""
    balance: float
    commission_rate: float


class LedgerItemResponse(BaseModel):
    """תנועת ארנק בודדת"""
    id: int
    entry_type: str
    amount: float
    balance_after: float
    description: Optional[str] = None
    created_at: str


class PaginatedLedgerResponse(BaseModel):
    """תנועות ארנק עם pagination"""
    items: List[LedgerItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    summary: dict


class UpdateCommissionRateRequest(BaseModel):
    """בקשת עדכון אחוז עמלה — ערך באחוזים (6–12)"""
    commission_rate_percent: int

    @field_validator("commission_rate_percent")
    @classmethod
    def validate_range(cls, v: int) -> int:
        if v < 6 or v > 12:
            raise ValueError("אחוז עמלה חייב להיות בין 6 ל-12")
        return v


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=WalletResponse,
    summary="יתרת ארנק",
    description="מחזיר יתרה ושיעור עמלה של ארנק התחנה.",
    tags=["Panel - ארנק"],
)
async def get_wallet(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> WalletResponse:
    """יתרת ארנק"""
    station_service = StationService(db)
    wallet = await station_service.get_station_wallet(auth.station_id)
    return WalletResponse(
        balance=wallet.balance,
        commission_rate=wallet.commission_rate,
    )


@router.get(
    "/ledger",
    response_model=PaginatedLedgerResponse,
    summary="היסטוריית תנועות ארנק",
    description="היסטוריית תנועות עם pagination, סינון לפי סוג תנועה וטווח תאריכים.",
    tags=["Panel - ארנק"],
)
async def get_ledger(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    entry_type: Optional[str] = Query(None, description="סוג תנועה (commission_credit/manual_charge/withdrawal)"),
    date_from: Optional[str] = Query(None, description="מתאריך (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="עד תאריך (YYYY-MM-DD)"),
) -> PaginatedLedgerResponse:
    """תנועות ארנק עם סינון ו-pagination"""
    station_id = auth.station_id

    # בניית תנאי where
    base_where = [StationLedger.station_id == station_id]

    if entry_type:
        try:
            et = StationLedgerEntryType(entry_type)
            base_where.append(StationLedger.entry_type == et)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"סוג תנועה לא תקין: {entry_type}",
            )

    dt_from = parse_date_param(date_from, "date_from")
    if dt_from:
        base_where.append(StationLedger.created_at >= dt_from)

    dt_to = parse_date_param(date_to, "date_to", end_of_day=True)
    if dt_to:
        base_where.append(StationLedger.created_at <= dt_to)

    # ספירה
    count_result = await db.execute(
        select(func.count(StationLedger.id)).where(*base_where)
    )
    total = count_result.scalar() or 0

    # שליפה
    offset = (page - 1) * page_size
    result = await db.execute(
        select(StationLedger)
        .where(*base_where)
        .order_by(StationLedger.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    entries = list(result.scalars().all())

    # סיכום לפי סוג תנועה בטווח הנבחר
    summary_result = await db.execute(
        select(
            StationLedger.entry_type,
            func.coalesce(func.sum(StationLedger.amount), 0),
        )
        .where(*base_where)
        .group_by(StationLedger.entry_type)
    )
    summary = {row[0].value: row[1] for row in summary_result.all()}

    return PaginatedLedgerResponse(
        items=[
            LedgerItemResponse(
                id=e.id,
                entry_type=e.entry_type.value,
                amount=e.amount,
                balance_after=e.balance_after,
                description=e.description,
                created_at=e.created_at.isoformat() if e.created_at else "",
            )
            for e in entries
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
        summary=summary,
    )


@router.put(
    "/commission-rate",
    response_model=ActionResponse,
    summary="עדכון אחוז עמלה",
    description="עדכון אחוז העמלה של התחנה. ערך חוקי: 6%–12%.",
    responses={
        200: {"description": "אחוז העמלה עודכן בהצלחה"},
        400: {"description": "ערך לא תקין"},
        422: {"description": "שגיאת ולידציה"},
    },
    tags=["Panel - ארנק"],
)
async def update_commission_rate(
    body: UpdateCommissionRateRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> ActionResponse:
    """עדכון אחוז עמלה של תחנה"""
    station_service = StationService(db)

    # המרה מאחוזים (6–12) לערך עשרוני (0.06–0.12)
    new_rate = body.commission_rate_percent / 100

    success, message = await station_service.update_commission_rate(
        auth.station_id, new_rate,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    logger.info(
        "אחוז עמלה עודכן דרך הפאנל",
        extra_data={
            "station_id": auth.station_id,
            "user_id": auth.user_id,
            "new_rate_percent": body.commission_rate_percent,
        },
    )
    return ActionResponse(success=True, message=message)
