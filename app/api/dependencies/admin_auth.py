"""
אימות אדמין — מפתח API או JWT טלגרם.

תומך בשתי שיטות אימות:
1. מפתח API סטטי (X-Admin-API-Key header) — לכלים אוטומטיים
2. JWT Bearer token עם role=admin — לכניסה דרך Telegram Login Widget

שימוש:
    @router.get("/debug/circuit-breakers")
    async def circuit_breakers(
        _: None = Depends(require_admin),
    ):
        ...
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import verify_token
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)
_bearer_scheme = HTTPBearer(auto_error=False)


async def require_admin(
    api_key: str | None = Depends(_api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """
    ולידציה של גישת אדמין — מפתח API או JWT טלגרם.

    בודק קודם API Key, אם לא תקין — בודק Bearer JWT עם role=admin.
    זורק 401 אם אין אמצעי אימות, 403 אם לא תקין.

    שתי השיטות מוצהרות כ-security dependencies כדי ש-OpenAPI/Swagger
    יציג את שתי האפשרויות וישלח את ה-headers הנכונים.
    """
    # ניסיון 1: מפתח API
    if api_key:
        if settings.ADMIN_API_KEY and api_key == settings.ADMIN_API_KEY:
            return  # אימות הצליח
        # מפתח שגוי — לא חוסמים מיד, ממשיכים לבדוק JWT
        logger.info("מפתח API לא תואם — בודק Bearer JWT")

    # ניסיון 2: JWT Bearer token עם role=admin
    if bearer:
        token_data = verify_token(bearer.credentials)
        if token_data and token_data.role == "admin":
            return  # אימות הצליח
        logger.warning(
            "גישה ל-admin endpoint נדחתה — JWT לא תקין או תפקיד שגוי",
            extra_data={"role": token_data.role if token_data else None},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="טוקן לא תקין או שאינו שייך לאדמין",
        )

    # אם היה API key (אבל שגוי) ולא היה JWT — מחזיר 403
    if api_key:
        if not settings.ADMIN_API_KEY:
            logger.warning("גישה ל-admin endpoint נדחתה — ADMIN_API_KEY לא מוגדר")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ADMIN_API_KEY לא מוגדר בסביבה",
            )
        logger.warning("גישה ל-admin endpoint נדחתה — מפתח API שגוי")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="מפתח API לא תקין",
        )

    # אין אמצעי אימות כלל
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="נדרש אימות — מפתח API (X-Admin-API-Key) או JWT אדמין (Bearer token)",
    )


# תאימות לאחור — שם ישן
require_admin_api_key = require_admin
