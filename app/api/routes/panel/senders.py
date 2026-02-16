"""
ניהול שולחים — רשימת שולחים, שולחים מובילים, פרטי שולח, משלוחי שולח
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.validation import PhoneNumberValidator
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.db.models.delivery import DeliveryStatus
from app.domain.services.station_service import StationService

router = APIRouter()


# ==================== סכמות ====================


class SenderResponse(BaseModel):
    """שולח בודד עם סטטיסטיקות"""
    user_id: int
    name: str
    phone_masked: str
    platform: str
    is_active: bool
    created_at: str
    deliveries_count: int
    delivered_count: int
    active_deliveries_count: int
    total_volume: float
    last_delivery_at: Optional[str] = None


class PaginatedSendersResponse(BaseModel):
    """רשימת שולחים עם pagination"""
    items: List[SenderResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class SenderDetailResponse(BaseModel):
    """פרטי שולח מלאים עם סטטיסטיקות מפורטות"""
    user_id: int
    name: str
    phone_masked: str
    platform: str
    is_active: bool
    created_at: str
    deliveries_count: int
    delivered_count: int
    cancelled_count: int
    active_deliveries_count: int
    total_volume: float
    avg_fee: float
    first_delivery_at: Optional[str] = None
    last_delivery_at: Optional[str] = None


class TopSenderResponse(BaseModel):
    """שולח מוביל"""
    user_id: int
    name: str
    phone_masked: str
    deliveries_count: int
    delivered_count: int
    total_volume: float
    last_delivery_at: Optional[str] = None


class SenderDeliveryItemResponse(BaseModel):
    """משלוח בודד של שולח"""
    id: int
    pickup_address: str
    dropoff_address: str
    status: str
    fee: float
    courier_name: Optional[str] = None
    created_at: str
    delivered_at: Optional[str] = None


class PaginatedSenderDeliveriesResponse(BaseModel):
    """משלוחי שולח עם pagination"""
    items: List[SenderDeliveryItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# ==================== עזר ====================


def _format_datetime(dt: object) -> str:
    """המרת datetime לפורמט ISO — מחזיר מחרוזת ריקה אם None"""
    if dt is None:
        return ""
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _format_optional_datetime(dt: object) -> Optional[str]:
    """המרת datetime אופציונלי — מחזיר None אם אין ערך"""
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=PaginatedSendersResponse,
    summary="רשימת שולחים",
    description="רשימת שולחים שיצרו משלוחים בתחנה, כולל סטטיסטיקות לכל שולח.",
    responses={
        200: {"description": "רשימת שולחים עם סטטיסטיקות"},
    },
    tags=["Panel - שולחים"],
)
async def list_senders(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1, description="מספר עמוד"),
    page_size: int = Query(20, ge=1, le=100, description="פריטים בעמוד"),
    search: Optional[str] = Query(None, description="חיפוש לפי שם"),
    sort_by: str = Query("deliveries_count", description="מיון לפי: deliveries_count / last_delivery / name / total_volume"),
    sort_order: str = Query("desc", description="כיוון מיון: asc / desc"),
) -> PaginatedSendersResponse:
    """רשימת שולחים עם סטטיסטיקות ו-pagination"""
    # ולידציה של פרמטרי מיון
    valid_sort_fields = {"deliveries_count", "last_delivery", "name", "total_volume"}
    if sort_by not in valid_sort_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"שדה מיון לא תקין: {sort_by}. אפשרויות: {', '.join(valid_sort_fields)}",
        )
    if sort_order not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="כיוון מיון לא תקין. אפשרויות: asc, desc",
        )

    station_service = StationService(db)
    senders, total = await station_service.get_station_senders(
        station_id=auth.station_id,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    return PaginatedSendersResponse(
        items=[
            SenderResponse(
                user_id=s["user_id"],
                name=s["name"],
                phone_masked=PhoneNumberValidator.mask(s["phone_number"]) if s["phone_number"] else "",
                platform=s["platform"] or "",
                is_active=s["is_active"],
                created_at=_format_datetime(s["created_at"]),
                deliveries_count=s["deliveries_count"],
                delivered_count=s["delivered_count"],
                active_deliveries_count=s["active_deliveries_count"],
                total_volume=s["total_volume"],
                last_delivery_at=_format_optional_datetime(s["last_delivery_at"]),
            )
            for s in senders
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get(
    "/top",
    response_model=List[TopSenderResponse],
    summary="שולחים מובילים",
    description="רשימת השולחים המובילים לפי מספר משלוחים שנמסרו.",
    responses={
        200: {"description": "רשימת שולחים מובילים"},
    },
    tags=["Panel - שולחים"],
)
async def top_senders(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(10, ge=1, le=50, description="מספר שולחים להחזיר"),
) -> List[TopSenderResponse]:
    """שולחים מובילים לפי משלוחים שנמסרו"""
    station_service = StationService(db)
    senders = await station_service.get_top_senders(
        station_id=auth.station_id,
        limit=limit,
    )

    return [
        TopSenderResponse(
            user_id=s["user_id"],
            name=s["name"],
            phone_masked=PhoneNumberValidator.mask(s["phone_number"]) if s["phone_number"] else "",
            deliveries_count=s["deliveries_count"],
            delivered_count=s["delivered_count"],
            total_volume=s["total_volume"],
            last_delivery_at=_format_optional_datetime(s["last_delivery_at"]),
        )
        for s in senders
    ]


@router.get(
    "/{sender_id}",
    response_model=SenderDetailResponse,
    summary="פרטי שולח",
    description="פרטי שולח מלאים עם סטטיסטיקות מפורטות.",
    responses={
        200: {"description": "פרטי שולח"},
        404: {"description": "שולח לא נמצא"},
    },
    tags=["Panel - שולחים"],
)
async def get_sender_detail(
    sender_id: int,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> SenderDetailResponse:
    """פרטי שולח בודד עם סטטיסטיקות מפורטות"""
    station_service = StationService(db)
    sender = await station_service.get_sender_details(
        station_id=auth.station_id,
        sender_id=sender_id,
    )

    if not sender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="שולח לא נמצא בתחנה זו",
        )

    return SenderDetailResponse(
        user_id=sender["user_id"],
        name=sender["name"],
        phone_masked=PhoneNumberValidator.mask(sender["phone_number"]) if sender["phone_number"] else "",
        platform=sender["platform"] or "",
        is_active=sender["is_active"],
        created_at=_format_datetime(sender["created_at"]),
        deliveries_count=sender["deliveries_count"],
        delivered_count=sender["delivered_count"],
        cancelled_count=sender["cancelled_count"],
        active_deliveries_count=sender["active_deliveries_count"],
        total_volume=sender["total_volume"],
        avg_fee=sender["avg_fee"],
        first_delivery_at=_format_optional_datetime(sender["first_delivery_at"]),
        last_delivery_at=_format_optional_datetime(sender["last_delivery_at"]),
    )


@router.get(
    "/{sender_id}/deliveries",
    response_model=PaginatedSenderDeliveriesResponse,
    summary="משלוחי שולח",
    description="רשימת משלוחים של שולח ספציפי בתחנה.",
    responses={
        200: {"description": "רשימת משלוחים"},
    },
    tags=["Panel - שולחים"],
)
async def list_sender_deliveries(
    sender_id: int,
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1, description="מספר עמוד"),
    page_size: int = Query(20, ge=1, le=100, description="פריטים בעמוד"),
    status_filter: Optional[str] = Query(None, description="סטטוס לסינון"),
) -> PaginatedSenderDeliveriesResponse:
    """רשימת משלוחים של שולח עם pagination"""
    # ולידציית סטטוס
    delivery_status = None
    if status_filter:
        try:
            delivery_status = DeliveryStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"סטטוס לא תקין: {status_filter}",
            )

    station_service = StationService(db)
    deliveries, total = await station_service.get_sender_deliveries(
        station_id=auth.station_id,
        sender_id=sender_id,
        page=page,
        page_size=page_size,
        status_filter=delivery_status,
    )

    return PaginatedSenderDeliveriesResponse(
        items=[
            SenderDeliveryItemResponse(
                id=d.id,
                pickup_address=d.pickup_address,
                dropoff_address=d.dropoff_address,
                status=d.status.value,
                fee=float(d.fee),
                courier_name=(
                    d.courier.name or d.courier.full_name or "לא צוין"
                ) if d.courier else None,
                created_at=d.created_at.isoformat() if d.created_at else "",
                delivered_at=d.delivered_at.isoformat() if d.delivered_at else None,
            )
            for d in deliveries
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )
