"""
דשבורד — סיכום נתוני תחנה
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType
from app.domain.services.station_service import StationService

router = APIRouter()


class DashboardResponse(BaseModel):
    """נתוני דשבורד"""
    station_name: str
    # משלוחים
    active_deliveries_count: int
    today_deliveries_count: int
    today_delivered_count: int
    # פיננסי
    wallet_balance: float
    commission_rate: float
    today_revenue: float
    # כוח אדם
    active_dispatchers_count: int
    blacklisted_count: int


@router.get(
    "",
    response_model=DashboardResponse,
    summary="נתוני דשבורד תחנה",
    description="מחזיר סיכום נתונים מרכזיים: משלוחים פעילים, סטטיסטיקות יומיות, ארנק, סדרנים.",
    responses={200: {"description": "נתוני דשבורד"}},
    tags=["Panel - דשבורד"],
)
async def get_dashboard(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    """נתוני דשבורד — שימוש ב-StationService + שאילתות מותאמות"""
    station_service = StationService(db)
    station_id = auth.station_id

    station = await station_service.get_station(station_id)
    wallet = await station_service.get_station_wallet(station_id)
    active = await station_service.get_station_active_deliveries(station_id)
    dispatchers = await station_service.get_dispatchers(station_id)
    blacklist = await station_service.get_blacklist(station_id)

    # סטטיסטיקות יומיות — לפי שעון ישראל (UTC+2/+3)
    # הערה: ישראל UTC+2 בחורף, UTC+3 בקיץ. משתמשים ב-+2 כבסיס עקבי.
    _ISRAEL_OFFSET = timezone(timedelta(hours=2))
    now_israel = datetime.now(_ISRAEL_OFFSET)
    today_start = now_israel.replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).astimezone(timezone.utc).replace(tzinfo=None)

    # משלוחים שנוצרו היום
    today_count_result = await db.execute(
        select(func.count(Delivery.id)).where(
            Delivery.station_id == station_id,
            Delivery.created_at >= today_start,
        )
    )
    today_deliveries_count = today_count_result.scalar() or 0

    # משלוחים שנמסרו היום
    today_delivered_result = await db.execute(
        select(func.count(Delivery.id)).where(
            Delivery.station_id == station_id,
            Delivery.status == DeliveryStatus.DELIVERED,
            Delivery.delivered_at >= today_start,
        )
    )
    today_delivered_count = today_delivered_result.scalar() or 0

    # הכנסות היום (סכום כל תנועות הלדג'ר מהיום)
    today_revenue_result = await db.execute(
        select(func.coalesce(func.sum(StationLedger.amount), 0.0)).where(
            StationLedger.station_id == station_id,
            StationLedger.created_at >= today_start,
            StationLedger.entry_type.in_([
                StationLedgerEntryType.COMMISSION_CREDIT,
                StationLedgerEntryType.MANUAL_CHARGE,
            ]),
        )
    )
    today_revenue = today_revenue_result.scalar() or 0.0

    return DashboardResponse(
        station_name=station.name if station else "",
        active_deliveries_count=len(active),
        today_deliveries_count=today_deliveries_count,
        today_delivered_count=today_delivered_count,
        wallet_balance=wallet.balance,
        commission_rate=wallet.commission_rate,
        today_revenue=today_revenue,
        active_dispatchers_count=len(dispatchers),
        blacklisted_count=len(blacklist),
    )
