"""
wa.me Link Generation — יצירת קישורי Click-to-Chat.

בזרימה ההיברידית, הודעות קבוצה (Arm B / WPPConnect) מכילות
קישור wa.me שפותח צ'אט פרטי עם המספר הרשמי (Arm A / Cloud API).
"""
from __future__ import annotations

from app.core.config import settings


def generate_capture_link(delivery_token: str) -> str | None:
    """יצירת קישור wa.me לתפיסת משלוח דרך Cloud API.

    Args:
        delivery_token: טוקן ייחודי של המשלוח.

    Returns:
        קישור wa.me מלא, או None אם מצב היברידי לא פעיל.
    """
    if not settings.WHATSAPP_HYBRID_MODE or not settings.WHATSAPP_CLOUD_API_PHONE_NUMBER:
        return None
    phone = settings.WHATSAPP_CLOUD_API_PHONE_NUMBER
    return f"https://wa.me/{phone}?text=capture_{delivery_token}"


def generate_menu_link() -> str | None:
    """יצירת קישור wa.me לפתיחת תפריט ראשי בפרטי.

    Returns:
        קישור wa.me, או None אם מצב היברידי לא פעיל.
    """
    if not settings.WHATSAPP_HYBRID_MODE or not settings.WHATSAPP_CLOUD_API_PHONE_NUMBER:
        return None
    phone = settings.WHATSAPP_CLOUD_API_PHONE_NUMBER
    return f"https://wa.me/{phone}?text=menu"
