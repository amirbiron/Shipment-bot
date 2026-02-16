"""
סכמות ו-helpers משותפים לפאנל — מודלים שחוזרים בכמה קבצים
"""
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel


class ActionResponse(BaseModel):
    """תגובת פעולה"""
    success: bool
    message: str


class BulkResultItem(BaseModel):
    """תוצאת הוספה בודדת"""
    phone_masked: str
    success: bool
    message: str


# ==================== סכמות מולטי-תחנה (סעיף 9) ====================


class StationSummary(BaseModel):
    """סיכום נתוני תחנה בודדת"""
    station_id: int
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


class MultiStationTotals(BaseModel):
    """סכומים מצטברים של כל התחנות"""
    total_active_deliveries: int
    total_today_deliveries: int
    total_today_delivered: int
    total_wallet_balance: float
    total_today_revenue: float
    total_active_dispatchers: int
    total_blacklisted: int


class StationsListResponse(BaseModel):
    """תגובת רשימת תחנות עם סטטיסטיקות מסכמות"""
    current_station_id: int
    stations: List[StationSummary]
    totals: MultiStationTotals


class MultiDashboardResponse(BaseModel):
    """תגובת דשבורד מרובה-תחנות — כולל נתונים לכל תחנה וסכומים"""
    current_station_id: int
    stations: List[StationSummary]
    totals: MultiStationTotals


# ==================== Helpers ====================


def parse_date_param(value: Optional[str], param_name: str, end_of_day: bool = False) -> Optional[datetime]:
    """פרסור פרמטר תאריך מ-query string — מחזיר datetime או None, זורק 400 על פורמט שגוי"""
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"פורמט תאריך לא תקין ב-{param_name}. יש להשתמש ב-YYYY-MM-DD",
        )
