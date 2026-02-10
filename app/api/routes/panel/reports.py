"""
דוחות — דוח גבייה, דוח הכנסות, ייצוא CSV
"""
import calendar
import csv
import io
from datetime import datetime
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
from app.db.models.manual_charge import ManualCharge
from app.domain.services.station_service import StationService

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
    """דוח גבייה — מבוסס על StationService.get_collection_report עם הרחבה"""
    station_id = auth.station_id

    # חישוב תחילת מחזור
    if cycle_start:
        try:
            cs = datetime.strptime(cycle_start, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="פורמט תאריך לא תקין",
            )
    else:
        cs = StationService._get_billing_cycle_start()

    # סוף מחזור — תחילת החודש הבא (מותאם ליום האחרון בחודש)
    if cs.month == 12:
        next_month, next_year = 1, cs.year + 1
    else:
        next_month, next_year = cs.month + 1, cs.year
    # מוודאים שהיום לא חורג מהמקסימום בחודש הבא
    max_day = calendar.monthrange(next_year, next_month)[1]
    ce = cs.replace(year=next_year, month=next_month, day=min(cs.day, max_day))

    # שליפת חיובים בטווח
    result = await db.execute(
        select(ManualCharge).where(
            ManualCharge.station_id == station_id,
            ManualCharge.created_at >= cs,
            ManualCharge.created_at < ce,
        ).order_by(ManualCharge.created_at.desc())
    )
    charges = list(result.scalars().all())

    # קיבוץ לפי שם נהג
    report: dict[str, dict] = {}
    for charge in charges:
        name = charge.driver_name
        if name not in report:
            report[name] = {"total_debt": 0.0, "charge_count": 0}
        report[name]["total_debt"] += charge.amount
        report[name]["charge_count"] += 1

    items = [
        CollectionReportItem(
            driver_name=name,
            total_debt=data["total_debt"],
            charge_count=data["charge_count"],
        )
        for name, data in report.items()
        if data["total_debt"] > 0
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

    # ברירת מחדל — 30 ימים אחרונים
    now = datetime.utcnow()
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="פורמט תאריך לא תקין")
    else:
        dt_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="פורמט תאריך לא תקין")
    else:
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
