"""
דוחות — דוח גבייה, דוח הכנסות, ייצוא CSV
"""
import calendar
import csv
import io
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType
from app.domain.services.station_service import StationService
from app.api.routes.panel.schemas import parse_date_param

router = APIRouter()


# ==================== סכמות ====================


class CollectionReportItem(BaseModel):
    """שורה בדוח גבייה"""
    driver_name: str
    total_debt: float
    charge_count: int


class CollectionReportResponse(BaseModel):
    """דוח גבייה"""
    items: List[CollectionReportItem]
    total_debt: float
    cycle_start: str
    cycle_end: str


class RevenueReportResponse(BaseModel):
    """דוח הכנסות"""
    total_commissions: float
    total_manual_charges: float
    total_withdrawals: float
    net_total: float
    date_from: str
    date_to: str


# ==================== Endpoints ====================


@router.get(
    "/collection",
    response_model=CollectionReportResponse,
    summary="דוח גבייה",
    description="דוח חובות נהגים לתחנה במחזור חיוב ספציפי.",
    tags=["Panel - דוחות"],
)
async def get_collection_report(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    cycle_start: Optional[str] = Query(None, description="תחילת מחזור (YYYY-MM-DD). ברירת מחדל: מחזור נוכחי"),
) -> CollectionReportResponse:
    """דוח גבייה — מבוסס על StationService"""
    station_id = auth.station_id
    station_service = StationService(db)

    # חישוב תחילת מחזור
    cs = parse_date_param(cycle_start, "cycle_start")
    if not cs:
        cs = StationService.get_billing_cycle_start()

    # סוף מחזור — תחילת החודש הבא (מותאם ליום האחרון בחודש)
    if cs.month == 12:
        next_month, next_year = 1, cs.year + 1
    else:
        next_month, next_year = cs.month + 1, cs.year
    max_day = calendar.monthrange(next_year, next_month)[1]
    ce = cs.replace(year=next_year, month=next_month, day=min(cs.day, max_day))

    # שליפת דוח מה-service
    report_data = await station_service.get_collection_report_for_period(station_id, cs, ce)

    items = [
        CollectionReportItem(
            driver_name=item["driver_name"],
            total_debt=item["total_debt"],
            charge_count=item["charge_count"],
        )
        for item in report_data
    ]

    total_debt = sum(item.total_debt for item in items)

    return CollectionReportResponse(
        items=items,
        total_debt=total_debt,
        cycle_start=cs.strftime("%Y-%m-%d"),
        cycle_end=ce.strftime("%Y-%m-%d"),
    )


@router.get(
    "/collection/export",
    summary="ייצוא דוח גבייה ל-CSV",
    description="מייצא את דוח הגבייה כקובץ CSV (עם תמיכה בעברית ב-Excel).",
    tags=["Panel - דוחות"],
)
async def export_collection_report(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    cycle_start: Optional[str] = Query(None, description="תחילת מחזור (YYYY-MM-DD)"),
) -> StreamingResponse:
    """ייצוא CSV — עם BOM לתמיכה בעברית ב-Excel, generator-based streaming"""
    # שימוש חוזר בלוגיקת הדוח
    report_data = await get_collection_report(auth=auth, db=db, cycle_start=cycle_start)

    def _generate_csv():
        """Generator שמייצר שורות CSV בהדרגה — חוסך זיכרון בדוחות גדולים"""
        output = io.StringIO()
        writer = csv.writer(output)

        # BOM לתמיכה ב-Excel עברית
        output.write("\ufeff")
        writer.writerow(["שם נהג", "סה\"כ חוב", "מספר חיובים"])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for item in report_data.items:
            writer.writerow([item.driver_name, f"{item.total_debt:.2f}", item.charge_count])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

        writer.writerow([])
        writer.writerow(["סה\"כ", f"{report_data.total_debt:.2f}", ""])
        yield output.getvalue()

    filename = f"collection_report_{report_data.cycle_start}.csv"
    return StreamingResponse(
        _generate_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get(
    "/revenue",
    response_model=RevenueReportResponse,
    summary="דוח הכנסות",
    description="סיכום הכנסות התחנה בטווח תאריכים.",
    tags=["Panel - דוחות"],
)
async def get_revenue_report(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    date_from: Optional[str] = Query(None, description="מתאריך (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="עד תאריך (YYYY-MM-DD)"),
) -> RevenueReportResponse:
    """דוח הכנסות — סיכום לפי סוגי תנועה"""
    station_id = auth.station_id

    # ברירת מחדל — תחילת החודש עד עכשיו
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    dt_from = parse_date_param(date_from, "date_from")
    if not dt_from:
        dt_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    dt_to = parse_date_param(date_to, "date_to", end_of_day=True)
    if not dt_to:
        dt_to = now

    # סיכום לפי סוג תנועה
    result = await db.execute(
        select(
            StationLedger.entry_type,
            func.coalesce(func.sum(StationLedger.amount), 0.0),
        )
        .where(
            StationLedger.station_id == station_id,
            StationLedger.created_at >= dt_from,
            StationLedger.created_at <= dt_to,
        )
        .group_by(StationLedger.entry_type)
    )
    sums = {row[0]: row[1] for row in result.all()}

    commissions = sums.get(StationLedgerEntryType.COMMISSION_CREDIT, 0.0)
    manual = sums.get(StationLedgerEntryType.MANUAL_CHARGE, 0.0)
    withdrawals = sums.get(StationLedgerEntryType.WITHDRAWAL, 0.0)

    return RevenueReportResponse(
        total_commissions=commissions,
        total_manual_charges=manual,
        total_withdrawals=withdrawals,
        net_total=commissions + manual - withdrawals,
        date_from=dt_from.strftime("%Y-%m-%d"),
        date_to=dt_to.strftime("%Y-%m-%d"),
    )
