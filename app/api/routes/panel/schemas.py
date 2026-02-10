"""
סכמות ו-helpers משותפים לפאנל — מודלים שחוזרים בכמה קבצים
"""
from datetime import datetime
from typing import Optional

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
