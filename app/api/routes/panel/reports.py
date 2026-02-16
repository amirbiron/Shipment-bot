"""
דוחות — דוח גבייה, דוח הכנסות, ייצוא CSV/Excel, דוח רווח/הפסד, דוח חודשי
"""
import calendar
import csv
import io
from decimal import Decimal
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.api.dependencies.auth import get_current_station_owner
from app.db.database import get_db
from app.db.models.station import Station
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType
from app.domain.services.station_service import StationService
from app.domain.services.export_service import (
    generate_collection_report_excel,
    generate_revenue_report_excel,
    generate_profit_loss_excel,
    generate_monthly_summary_excel,
)
from app.api.routes.panel.schemas import parse_date_param
from app.core.logging import get_logger

logger = get_logger(__name__)

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


class ProfitLossMonthItem(BaseModel):
    """שורת חודש בדוח רווח/הפסד"""
    month: str
    commissions: float
    manual_charges: float
    withdrawals: float
    net: float


class ProfitLossResponse(BaseModel):
    """דוח רווח/הפסד"""
    months: List[ProfitLossMonthItem]
    total_commissions: float
    total_manual_charges: float
    total_withdrawals: float
    total_net: float
    date_from: str
    date_to: str


class MonthlySummaryResponse(BaseModel):
    """דוח חודשי מסכם"""
    month: str
    station_name: str
    # פיננסי
    commissions: float
    manual_charges: float
    withdrawals: float
    net: float
    # משלוחים
    total_deliveries: int
    delivered_count: int
    cancelled_count: int
    open_count: int
    # גבייה
    total_debt: float
    debtors_count: int


# ==================== פונקציות עזר ====================


def _compute_cycle_dates(
    cycle_start_str: Optional[str],
) -> tuple[datetime, datetime]:
    """חישוב תחילת וסוף מחזור חיוב"""
    cs = parse_date_param(cycle_start_str, "cycle_start")
    if not cs:
        cs = StationService.get_billing_cycle_start()

    if cs.month == 12:
        next_month, next_year = 1, cs.year + 1
    else:
        next_month, next_year = cs.month + 1, cs.year
    max_day = calendar.monthrange(next_year, next_month)[1]
    ce = cs.replace(year=next_year, month=next_month, day=min(cs.day, max_day))
    return cs, ce


def _parse_month_range(
    month: Optional[str],
) -> tuple[datetime, datetime, str]:
    """פירוש חודש (YYYY-MM) לטווח תאריכים (dt_from, dt_to, month_str)"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if month:
        try:
            dt_from = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="פורמט חודש לא תקין. יש להשתמש ב-YYYY-MM",
            )
    else:
        if now.month == 1:
            dt_from = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            dt_from = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)

    last_day = calendar.monthrange(dt_from.year, dt_from.month)[1]
    dt_to = dt_from.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    month_str = dt_from.strftime("%Y-%m")
    return dt_from, dt_to, month_str


async def _get_station_name(db: AsyncSession, station_id: int) -> str:
    """שליפת שם התחנה"""
    result = await db.execute(
        select(Station.name).where(Station.id == station_id)
    )
    name = result.scalar_one_or_none()
    return name or ""


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

    cs, ce = _compute_cycle_dates(cycle_start)

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
    "/collection/export-xlsx",
    summary="ייצוא דוח גבייה ל-Excel",
    description="מייצא את דוח הגבייה כקובץ Excel מעוצב עם כותרות, סיכומים ותמיכה בעברית.",
    responses={
        200: {"description": "קובץ Excel", "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}},
    },
    tags=["Panel - דוחות"],
)
async def export_collection_report_xlsx(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    cycle_start: Optional[str] = Query(None, description="תחילת מחזור (YYYY-MM-DD)"),
) -> Response:
    """ייצוא דוח גבייה כ-Excel מעוצב"""
    report_data = await get_collection_report(auth=auth, db=db, cycle_start=cycle_start)
    station_name = await _get_station_name(db, auth.station_id)

    items = [
        {"driver_name": i.driver_name, "total_debt": i.total_debt, "charge_count": i.charge_count}
        for i in report_data.items
    ]

    xlsx_bytes = generate_collection_report_excel(
        items=items,
        total_debt=report_data.total_debt,
        cycle_start=report_data.cycle_start,
        cycle_end=report_data.cycle_end,
        station_name=station_name,
    )

    filename = f"collection_report_{report_data.cycle_start}.xlsx"
    logger.info(
        "ייצוא דוח גבייה ל-Excel",
        extra_data={"station_id": auth.station_id, "items_count": len(items)},
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
            func.coalesce(func.sum(StationLedger.amount), 0),
        )
        .where(
            StationLedger.station_id == station_id,
            StationLedger.created_at >= dt_from,
            StationLedger.created_at <= dt_to,
        )
        .group_by(StationLedger.entry_type)
    )
    sums = {row[0]: row[1] for row in result.all()}

    commissions = sums.get(StationLedgerEntryType.COMMISSION_CREDIT, Decimal("0"))
    manual = sums.get(StationLedgerEntryType.MANUAL_CHARGE, Decimal("0"))
    withdrawals = sums.get(StationLedgerEntryType.WITHDRAWAL, Decimal("0"))

    return RevenueReportResponse(
        total_commissions=commissions,
        total_manual_charges=manual,
        total_withdrawals=withdrawals,
        net_total=commissions + manual - withdrawals,
        date_from=dt_from.strftime("%Y-%m-%d"),
        date_to=dt_to.strftime("%Y-%m-%d"),
    )


@router.get(
    "/revenue/export-xlsx",
    summary="ייצוא דוח הכנסות ל-Excel",
    description="מייצא את דוח ההכנסות כקובץ Excel מעוצב.",
    responses={
        200: {"description": "קובץ Excel", "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}},
    },
    tags=["Panel - דוחות"],
)
async def export_revenue_report_xlsx(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    date_from: Optional[str] = Query(None, description="מתאריך (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="עד תאריך (YYYY-MM-DD)"),
) -> Response:
    """ייצוא דוח הכנסות כ-Excel מעוצב"""
    report_data = await get_revenue_report(auth=auth, db=db, date_from=date_from, date_to=date_to)
    station_name = await _get_station_name(db, auth.station_id)

    xlsx_bytes = generate_revenue_report_excel(
        total_commissions=report_data.total_commissions,
        total_manual_charges=report_data.total_manual_charges,
        total_withdrawals=report_data.total_withdrawals,
        net_total=report_data.net_total,
        date_from=report_data.date_from,
        date_to=report_data.date_to,
        station_name=station_name,
    )

    filename = f"revenue_report_{report_data.date_from}_{report_data.date_to}.xlsx"
    logger.info(
        "ייצוא דוח הכנסות ל-Excel",
        extra_data={"station_id": auth.station_id},
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ==================== דוח רווח/הפסד ====================


@router.get(
    "/profit-loss",
    response_model=ProfitLossResponse,
    summary="דוח רווח/הפסד",
    description="דוח רווח/הפסד חודשי — פירוט הכנסות והוצאות לכל חודש בטווח תאריכים.",
    tags=["Panel - דוחות"],
)
async def get_profit_loss_report(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    date_from: Optional[str] = Query(None, description="מתאריך (YYYY-MM-DD). ברירת מחדל: 6 חודשים אחורה"),
    date_to: Optional[str] = Query(None, description="עד תאריך (YYYY-MM-DD). ברירת מחדל: היום"),
) -> ProfitLossResponse:
    """דוח רווח/הפסד — פירוט חודשי"""
    station_id = auth.station_id
    station_service = StationService(db)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    dt_from = parse_date_param(date_from, "date_from")
    if not dt_from:
        # ברירת מחדל — 6 חודשים אחורה
        month = now.month - 6
        year = now.year
        if month <= 0:
            month += 12
            year -= 1
        dt_from = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)

    dt_to = parse_date_param(date_to, "date_to", end_of_day=True)
    if not dt_to:
        dt_to = now

    months_data = await station_service.get_profit_loss_report(station_id, dt_from, dt_to)

    month_items = [ProfitLossMonthItem(**m) for m in months_data]

    # סיכומים
    total_commissions = sum(m.commissions for m in month_items)
    total_manual_charges = sum(m.manual_charges for m in month_items)
    total_withdrawals = sum(m.withdrawals for m in month_items)

    return ProfitLossResponse(
        months=month_items,
        total_commissions=total_commissions,
        total_manual_charges=total_manual_charges,
        total_withdrawals=total_withdrawals,
        total_net=total_commissions + total_manual_charges - total_withdrawals,
        date_from=dt_from.strftime("%Y-%m-%d"),
        date_to=dt_to.strftime("%Y-%m-%d"),
    )


@router.get(
    "/profit-loss/export-xlsx",
    summary="ייצוא דוח רווח/הפסד ל-Excel",
    description="מייצא את דוח הרווח/הפסד כקובץ Excel מעוצב עם פירוט חודשי.",
    responses={
        200: {"description": "קובץ Excel", "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}},
    },
    tags=["Panel - דוחות"],
)
async def export_profit_loss_xlsx(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    date_from: Optional[str] = Query(None, description="מתאריך (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="עד תאריך (YYYY-MM-DD)"),
) -> Response:
    """ייצוא דוח רווח/הפסד כ-Excel מעוצב"""
    report_data = await get_profit_loss_report(auth=auth, db=db, date_from=date_from, date_to=date_to)
    station_name = await _get_station_name(db, auth.station_id)

    revenue_by_month = [
        {
            "month": m.month,
            "commissions": m.commissions,
            "manual_charges": m.manual_charges,
            "withdrawals": m.withdrawals,
            "net": m.net,
        }
        for m in report_data.months
    ]

    xlsx_bytes = generate_profit_loss_excel(
        revenue_by_month=revenue_by_month,
        date_from=report_data.date_from,
        date_to=report_data.date_to,
        station_name=station_name,
    )

    filename = f"profit_loss_{report_data.date_from}_{report_data.date_to}.xlsx"
    logger.info(
        "ייצוא דוח רווח/הפסד ל-Excel",
        extra_data={"station_id": auth.station_id, "months_count": len(report_data.months)},
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ==================== דוח חודשי מסכם ====================


@router.get(
    "/monthly-summary",
    response_model=MonthlySummaryResponse,
    summary="דוח חודשי מסכם",
    description="דוח חודשי מסכם — סטטיסטיקות משלוחים, הכנסות וגבייה לחודש ספציפי.",
    tags=["Panel - דוחות"],
)
async def get_monthly_summary(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    month: Optional[str] = Query(None, description="חודש (YYYY-MM). ברירת מחדל: חודש קודם"),
) -> MonthlySummaryResponse:
    """דוח חודשי מסכם — כל הנתונים לחודש אחד"""
    station_id = auth.station_id
    station_service = StationService(db)

    dt_from, dt_to, month_str = _parse_month_range(month)

    summary = await station_service.get_monthly_summary_data(station_id, dt_from, dt_to)
    station_name = await _get_station_name(db, station_id)

    return MonthlySummaryResponse(
        month=month_str,
        station_name=station_name,
        commissions=summary["revenue"]["commissions"],
        manual_charges=summary["revenue"]["manual_charges"],
        withdrawals=summary["revenue"]["withdrawals"],
        net=summary["revenue"]["net"],
        total_deliveries=summary["delivery_stats"]["total"],
        delivered_count=summary["delivery_stats"]["delivered"],
        cancelled_count=summary["delivery_stats"]["cancelled"],
        open_count=summary["delivery_stats"]["open"],
        total_debt=summary["total_debt"],
        debtors_count=len(summary["collection_data"]),
    )


@router.get(
    "/monthly-summary/export-xlsx",
    summary="ייצוא דוח חודשי ל-Excel",
    description="מייצא את הדוח החודשי כקובץ Excel מעוצב עם מספר גליונות (סיכום + גבייה).",
    responses={
        200: {"description": "קובץ Excel", "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}},
    },
    tags=["Panel - דוחות"],
)
async def export_monthly_summary_xlsx(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
    month: Optional[str] = Query(None, description="חודש (YYYY-MM). ברירת מחדל: חודש קודם"),
) -> Response:
    """ייצוא דוח חודשי כ-Excel מעוצב עם מספר גליונות"""
    station_id = auth.station_id
    station_service = StationService(db)

    # פירוש חודש לתאריכים
    dt_from, dt_to, month_str = _parse_month_range(month)

    summary = await station_service.get_monthly_summary_data(station_id, dt_from, dt_to)
    station_name = await _get_station_name(db, station_id)

    xlsx_bytes = generate_monthly_summary_excel(
        month=month_str,
        station_name=station_name,
        collection_items=summary["collection_data"],
        total_debt=summary["total_debt"],
        revenue_data=summary["revenue"],
        delivery_stats=summary["delivery_stats"],
    )

    filename = f"monthly_report_{month_str}.xlsx"
    logger.info(
        "ייצוא דוח חודשי ל-Excel",
        extra_data={"station_id": station_id, "month": month_str},
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get(
    "/monthly-summary/cached-xlsx",
    summary="הורדת דוח חודשי מוכן מהמטמון",
    description=(
        "מחזיר דוח חודשי Excel שהופק אוטומטית על ידי המשימה התקופתית (1 לכל חודש). "
        "אם הדוח לא קיים במטמון, מחזיר 404."
    ),
    responses={
        200: {
            "description": "קובץ Excel",
            "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}},
        },
        404: {"description": "דוח לא נמצא במטמון"},
    },
    tags=["Panel - דוחות"],
)
async def get_cached_monthly_report(
    auth: TokenPayload = Depends(get_current_station_owner),
    month: Optional[str] = Query(None, description="חודש (YYYY-MM). ברירת מחדל: חודש קודם"),
) -> Response:
    """הורדת דוח חודשי שהופק אוטומטית — נשמר ב-Redis למשך 30 יום"""
    import base64
    from app.core.redis_client import get_redis

    _, _, month_str = _parse_month_range(month)

    redis = await get_redis()
    cache_key = f"monthly_report:{auth.station_id}:{month_str}"
    encoded = await redis.get(cache_key)

    if not encoded:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"דוח חודשי לחודש {month_str} לא נמצא במטמון. הדוח מופק אוטומטית ב-1 לכל חודש.",
        )

    xlsx_bytes = base64.b64decode(encoded)
    filename = f"monthly_report_{month_str}.xlsx"
    logger.info(
        "הורדת דוח חודשי מהמטמון",
        extra_data={"station_id": auth.station_id, "month": month_str},
    )
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
