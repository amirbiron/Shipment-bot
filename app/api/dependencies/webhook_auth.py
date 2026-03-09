"""
אימות חתימת webhook נכנס מטלגרם.

טלגרם שולח את הכותרת ``X-Telegram-Bot-Api-Secret-Token``
עם כל בקשת webhook אם הוגדר ``secret_token`` בקריאת ``setWebhook``.
ה-dependency הזו מוודאת שהכותרת תואמת לטוקן שמוגדר בסביבה.

פיצ'ר 4: כולל רישום ניסיונות כושלים לחסימת IP אוטומטית.

שימוש:
    @router.post("/webhook")
    async def telegram_webhook(
        ...,
        _: None = Depends(verify_telegram_webhook_token),
    ):
        ...
"""
import hmac

from fastapi import Header, HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def verify_telegram_webhook_token(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> None:
    """
    אימות ``X-Telegram-Bot-Api-Secret-Token`` בבקשות webhook מטלגרם.

    - אם ``TELEGRAM_WEBHOOK_SECRET_TOKEN`` לא מוגדר — מדלג (אזהרה בלוג).
    - אם הכותרת חסרה או לא תואמת — 403 Forbidden.
    - פיצ'ר 4: רושם ניסיונות כושלים לחסימת IP אוטומטית.
    """
    expected = settings.TELEGRAM_WEBHOOK_SECRET_TOKEN
    if not expected:
        # טוקן לא מוגדר — אין אימות (אזהרה כבר נרשמת ב-startup)
        return

    from app.api.dependencies.webhook_signature import (
        _get_client_ip,
        _is_ip_blocked,
        _record_failed_attempt,
    )

    client_ip = _get_client_ip(request)

    # בדיקת חסימת IP — רק כשאימות מופעל
    if _is_ip_blocked(client_ip):
        logger.warning(
            "Telegram webhook: בקשה מ-IP חסום",
            extra_data={"client_ip": client_ip},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="IP חסום זמנית עקב ניסיונות אימות כושלים חוזרים",
        )

    if not x_telegram_bot_api_secret_token:
        _record_failed_attempt(client_ip)
        logger.warning(
            "בקשת webhook ללא כותרת X-Telegram-Bot-Api-Secret-Token",
            extra_data={"client_ip": client_ip},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="חסר טוקן אימות webhook",
        )

    # השוואה בטוחה מפני timing attacks
    if not hmac.compare_digest(x_telegram_bot_api_secret_token, expected):
        _record_failed_attempt(client_ip)
        logger.warning(
            "בקשת webhook עם טוקן שגוי",
            extra_data={"client_ip": client_ip},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="טוקן אימות webhook לא תקין",
        )
