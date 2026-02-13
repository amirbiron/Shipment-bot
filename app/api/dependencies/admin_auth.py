"""
אימות מפתח API עבור endpoints דיאגנוסטיים של אדמין.

שימוש:
    @router.get("/debug/circuit-breakers")
    async def circuit_breakers(
        _: None = Depends(require_admin_api_key),
    ):
        ...
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)


async def require_admin_api_key(
    api_key: str | None = Depends(_api_key_header),
) -> None:
    """
    ולידציה של מפתח API לגישת אדמין.

    זורק 401 אם המפתח חסר, 403 אם לא תואם.
    אם ADMIN_API_KEY לא מוגדר בסביבה — הגישה חסומה לחלוטין.
    """
    if not settings.ADMIN_API_KEY:
        logger.warning("גישה ל-admin endpoint נדחתה — ADMIN_API_KEY לא מוגדר")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ADMIN_API_KEY לא מוגדר בסביבה",
        )

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="חסר מפתח API — נדרש header: X-Admin-API-Key",
        )

    if api_key != settings.ADMIN_API_KEY:
        logger.warning("גישה ל-admin endpoint נדחתה — מפתח API שגוי")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="מפתח API לא תקין",
        )
