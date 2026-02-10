"""
סכמות משותפות לפאנל — מודלים שחוזרים בכמה קבצים
"""
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
