"""
אימות חתימת webhook נכנס מטלגרם.

טלגרם שולח את הכותרת ``X-Telegram-Bot-Api-Secret-Token``
עם כל בקשת webhook אם הוגדר ``secret_token`` בקריאת ``setWebhook``.
ה-dependency הזה מוודא שהכותרת תואמת לטוקן שמוגדר בסביבה.

שימוש:
    @router.post("/webhook")
    async def telegram_webhook(
        ...,
        _: None = Depends(verify_telegram_webhook_token),
    ):
        ...
"""
import hmac

from fastapi import Depends, Header, HTTPException, status

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def verify_telegram_webhook_token(
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> None:
    """
    אימות ``X-Telegram-Bot-Api-Secret-Token`` בבקשות webhook מטלגרם.

    - אם ``TELEGRAM_WEBHOOK_SECRET_TOKEN`` לא מוגדר — מדלג (אזהרה בלוג).
    - אם הכותרת חסרה או לא תואמת — 403 Forbidden.
    """
    expected = settings.TELEGRAM_WEBHOOK_SECRET_TOKEN
    if not expected:
        # טוקן לא מוגדר — אין אימות (אזהרה כבר נרשמת ב-startup)
        return

    if not x_telegram_bot_api_secret_token:
        logger.warning("בקשת webhook ללא כותרת X-Telegram-Bot-Api-Secret-Token")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="חסר טוקן אימות webhook",
        )

    # השוואה בטוחה מפני timing attacks
    if not hmac.compare_digest(x_telegram_bot_api_secret_token, expected):
        logger.warning("בקשת webhook עם טוקן שגוי")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="טוקן אימות webhook לא תקין",
        )
