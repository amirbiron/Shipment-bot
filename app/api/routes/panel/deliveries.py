"""
משלוחים — פעילים, היסטוריה, פרטי משלוח בודד (עם pagination וסינון)
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import TokenPayload
from app.core.validation import PhoneNumberValidator
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.db.models.delivery import Delivery, DeliveryStatus, ACTIVE_DELIVERY_STATUSES
from app.db.queries import delivery_with_relations
from app.api.routes.panel.schemas import parse_date_param

router = APIRouter()


# ==================== סכמות ====================


class DeliveryItemResponse(BaseModel):
    """משלוח בודד ברשימה"""
    id: int
    pickup_address: str
    dropoff_address: str
    status: str
    fee: float
    courier_name: Optional[str] = None
    sender_name: Optional[str] = None
    created_at: str
    delivered_at: Optional[str] = None


class PaginatedDeliveriesResponse(BaseModel):
    """משלוחים עם pagination"""
    items: List[DeliveryItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class DeliveryDetailResponse(BaseModel):
    """
    פרטי משלוח מלאים.

    שדות pickup_contact_phone / dropoff_contact_phone ממוסכים (4 ספרות אחרונות מוסתרות).
    מידע זה זמין לבעל תחנה בלבד לצורך תיאום — אין להציג למשתמשים אחרים.
    """
    id: int
    pickup_address: str
    pickup_contact_name: Optional[str] = None
    pickup_contact_phone: Optional[str] = None
    dropoff_address: str
    dropoff_contact_name: Optional[str] = None
    dropoff_contact_phone: Optional[str] = None
    status: str
    fee: float
    courier_name: Optional[str] = None
    sender_name: Optional[str] = None
    created_at: str
    captured_at: Optional[str] = None
    delivered_at: Optional[str] = None


# ==================== Endpoints ====================


@router.get(
    "/active",
    response_model=PaginatedDeliveriesResponse,
    summary="משלוחים פעילים",
    description="רשימת משלוחים פעילים של התחנה עם pagination.",
    tags=["Panel - משלוחים"],
)
async def list_active_deliveries(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1, description="מספר עמוד"),
    page_size: int = Query(20, ge=1, le=100, description="פריטים בעמוד"),
) -> PaginatedDeliveriesResponse:
    """משלוחים פעילים עם pagination"""
    station_id = auth.station_id

    # ספירה כוללת
    count_result = await db.execute(
        select(func.count(Delivery.id)).where(
            Delivery.station_id == station_id,
            Delivery.status.in_(ACTIVE_DELIVERY_STATUSES),
        )
    )
    total = count_result.scalar() or 0

    # שליפה עם pagination ו-joinedload
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Delivery)
        .options(*delivery_with_relations())
        .where(
            Delivery.station_id == station_id,
            Delivery.status.in_(ACTIVE_DELIVERY_STATUSES),
        )
        .order_by(Delivery.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    deliveries = list(result.scalars().unique().all())

    return PaginatedDeliveriesResponse(
        items=[_to_item(d) for d in deliveries],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get(
    "/history",
    response_model=PaginatedDeliveriesResponse,
    summary="היסטוריית משלוחים",
    description="היסטוריית משלוחים עם סינון לפי תאריכים וסטטוס.",
    tags=["Panel - משלוחים"],
)
async def list_delivery_history(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, description="סטטוס לסינון (open/captured/delivered/cancelled)"),
    date_from: Optional[str] = Query(None, description="מתאריך (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="עד תאריך (YYYY-MM-DD)"),
) -> PaginatedDeliveriesResponse:
    """היסטוריית משלוחים עם סינון"""
    station_id = auth.station_id

    # בניית שאילתה בסיסית
    base_where = [Delivery.station_id == station_id]

    if status_filter:
        try:
            ds = DeliveryStatus(status_filter)
            base_where.append(Delivery.status == ds)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"סטטוס לא תקין: {status_filter}",
            )
    else:
        # ברירת מחדל — רק משלוחים שהסתיימו
        base_where.append(Delivery.status.in_([
            DeliveryStatus.DELIVERED,
            DeliveryStatus.CANCELLED,
        ]))

    dt_from = parse_date_param(date_from, "date_from")
    if dt_from:
        base_where.append(Delivery.created_at >= dt_from)

    dt_to = parse_date_param(date_to, "date_to", end_of_day=True)
    if dt_to:
        base_where.append(Delivery.created_at <= dt_to)

    # ספירה
    count_result = await db.execute(
        select(func.count(Delivery.id)).where(*base_where)
    )
    total = count_result.scalar() or 0

    # שליפה
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Delivery)
        .options(*delivery_with_relations())
        .where(*base_where)
        .order_by(Delivery.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    deliveries = list(result.scalars().unique().all())

    return PaginatedDeliveriesResponse(
        items=[_to_item(d) for d in deliveries],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get(
    "/{delivery_id}",
    response_model=DeliveryDetailResponse,
    summary="פרטי משלוח",
    description="פרטי משלוח מלאים לפי מזהה.",
    responses={404: {"description": "משלוח לא נמצא"}},
    tags=["Panel - משלוחים"],
)
async def get_delivery_detail(
    delivery_id: int,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> DeliveryDetailResponse:
    """פרטי משלוח בודד — כולל ולידציה שהמשלוח שייך לתחנה"""
    result = await db.execute(
        select(Delivery)
        .options(*delivery_with_relations())
        .where(
            Delivery.id == delivery_id,
            Delivery.station_id == auth.station_id,
        )
    )
    delivery = result.scalars().unique().one_or_none()

    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="משלוח לא נמצא",
        )

    return DeliveryDetailResponse(
        id=delivery.id,
        pickup_address=delivery.pickup_address,
        pickup_contact_name=delivery.pickup_contact_name,
        pickup_contact_phone=(
            PhoneNumberValidator.mask(delivery.pickup_contact_phone)
            if delivery.pickup_contact_phone else None
        ),
        dropoff_address=delivery.dropoff_address,
        dropoff_contact_name=delivery.dropoff_contact_name,
        dropoff_contact_phone=(
            PhoneNumberValidator.mask(delivery.dropoff_contact_phone)
            if delivery.dropoff_contact_phone else None
        ),
        status=delivery.status.value,
        fee=delivery.fee,
        courier_name=(
            delivery.courier.name or delivery.courier.full_name or "לא צוין"
        ) if delivery.courier else None,
        sender_name=(
            delivery.sender.name or delivery.sender.full_name or "לא צוין"
        ) if delivery.sender else None,
        created_at=delivery.created_at.isoformat() if delivery.created_at else "",
        captured_at=delivery.captured_at.isoformat() if delivery.captured_at else None,
        delivered_at=delivery.delivered_at.isoformat() if delivery.delivered_at else None,
    )


# ==================== עזר ====================


def _to_item(d: Delivery) -> DeliveryItemResponse:
    """המרת מודל Delivery ל-response item"""
    return DeliveryItemResponse(
        id=d.id,
        pickup_address=d.pickup_address,
        dropoff_address=d.dropoff_address,
        status=d.status.value,
        fee=d.fee,
        courier_name=(
            d.courier.name or d.courier.full_name or "לא צוין"
        ) if d.courier else None,
        sender_name=(
            d.sender.name or d.sender.full_name or "לא צוין"
        ) if d.sender else None,
        created_at=d.created_at.isoformat() if d.created_at else "",
        delivered_at=d.delivered_at.isoformat() if d.delivered_at else None,
    )
