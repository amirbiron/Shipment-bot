"""
דשבורד מולטי-תחנה — רשימת תחנות, סטטיסטיקות מצטברות ומעבר בין תחנות

סעיף 9 מתוך issue #210:
- תצוגה השוואתית בין תחנות
- מעבר מהיר בין תחנות
- סטטיסטיקות מצטברות
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.api.dependencies.auth import get_current_station_owner
from app.api.routes.panel.schemas import (
    MultiDashboardResponse,
    MultiStationTotals,
    StationSummary,
    StationsListResponse,
)
from app.db.database import get_db
from app.domain.services.station_service import StationService
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ==================== Helpers ====================


def _build_totals(summaries: list[dict]) -> MultiStationTotals:
    """חישוב סכומים מצטברים מרשימת סיכומי תחנות"""
    return MultiStationTotals(
        total_active_deliveries=sum(s["active_deliveries_count"] for s in summaries),
        total_today_deliveries=sum(s["today_deliveries_count"] for s in summaries),
        total_today_delivered=sum(s["today_delivered_count"] for s in summaries),
        total_wallet_balance=sum(s["wallet_balance"] for s in summaries),
        total_today_revenue=sum(s["today_revenue"] for s in summaries),
        total_active_dispatchers=sum(s["active_dispatchers_count"] for s in summaries),
        total_blacklisted=sum(s["blacklisted_count"] for s in summaries),
    )


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=StationsListResponse,
    summary="רשימת תחנות הבעלים עם סטטיסטיקות",
    description=(
        "מחזיר את כל התחנות שבבעלות המשתמש המחובר, "
        "כולל נתוני דשבורד מסכמים לכל תחנה וסכומים מצטברים."
    ),
    responses={200: {"description": "רשימת תחנות עם סטטיסטיקות"}},
    tags=["Panel - מולטי-תחנה"],
)
async def list_stations(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> StationsListResponse:
    """רשימת כל התחנות שבבעלות המשתמש עם נתונים מסכמים"""
    station_service = StationService(db)
    summaries = await station_service.get_multi_station_summary(auth.user_id)

    logger.info(
        "רשימת תחנות נטענה",
        extra_data={
            "user_id": auth.user_id,
            "station_count": len(summaries),
        },
    )

    return StationsListResponse(
        current_station_id=auth.station_id,
        stations=[StationSummary(**s) for s in summaries],
        totals=_build_totals(summaries),
    )


@router.get(
    "/dashboard",
    response_model=MultiDashboardResponse,
    summary="דשבורד השוואתי מרובה-תחנות",
    description=(
        "מחזיר תצוגה השוואתית של כל התחנות שבבעלות המשתמש. "
        "כולל נתוני דשבורד מלאים לכל תחנה וסכומים מצטברים."
    ),
    responses={200: {"description": "דשבורד מולטי-תחנה"}},
    tags=["Panel - מולטי-תחנה"],
)
async def multi_station_dashboard(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> MultiDashboardResponse:
    """דשבורד השוואתי — נתוני כל התחנות שבבעלות המשתמש + סכומים מצטברים"""
    station_service = StationService(db)
    summaries = await station_service.get_multi_station_summary(auth.user_id)

    logger.info(
        "דשבורד מולטי-תחנה נטען",
        extra_data={
            "user_id": auth.user_id,
            "station_count": len(summaries),
        },
    )

    return MultiDashboardResponse(
        current_station_id=auth.station_id,
        stations=[StationSummary(**s) for s in summaries],
        totals=_build_totals(summaries),
    )
