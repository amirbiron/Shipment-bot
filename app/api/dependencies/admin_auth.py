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
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.core.auth import verify_token
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)


def _extract_bearer_token(request: Request) -> str | None:
    """חילוץ Bearer token מ-Authorization header"""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


async def require_admin(
    request: Request,
    api_key: str | None = Depends(_api_key_header),
) -> None:
    """
    ולידציה של גישת אדמין — מפתח API או JWT טלגרם.

    בודק קודם API Key, אם אין — בודק Bearer JWT עם role=admin.
    זורק 401 אם אין אמצעי אימות, 403 אם לא תקין.
    """
    # ניסיון 1: מפתח API
    if api_key:
        if not settings.ADMIN_API_KEY:
            logger.warning("גישה ל-admin endpoint נדחתה — ADMIN_API_KEY לא מוגדר")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ADMIN_API_KEY לא מוגדר בסביבה",
            )
        if api_key == settings.ADMIN_API_KEY:
            return  # אימות הצליח
        logger.warning("גישה ל-admin endpoint נדחתה — מפתח API שגוי")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="מפתח API לא תקין",
        )

    # ניסיון 2: JWT Bearer token עם role=admin
    bearer_token = _extract_bearer_token(request)
    if bearer_token:
        token_data = verify_token(bearer_token)
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

    # אין אמצעי אימות
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="נדרש אימות — מפתח API (X-Admin-API-Key) או JWT אדמין (Bearer token)",
    )


# תאימות לאחור — שם ישן
require_admin_api_key = require_admin
