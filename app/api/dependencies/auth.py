"""
FastAPI dependency לאימות בקשות לפאנל ווב

שימוש:
    @router.get("/dashboard")
    async def dashboard(
        auth: TokenPayload = Depends(get_current_station_owner),
        db: AsyncSession = Depends(get_db),
    ):
        station_id = auth.station_id
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_token, TokenPayload
from app.db.database import get_db
from app.domain.services.station_service import StationService
from app.core.logging import get_logger

logger = get_logger(__name__)

security = HTTPBearer()


async def get_current_station_owner(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> TokenPayload:
    """
    אימות JWT ווידוא שהמשתמש הוא בעל תחנה פעילה.

    מחזיר TokenPayload עם user_id, station_id, role.
    זורק 401 אם הטוקן לא תקין, 403 אם התחנה לא פעילה.
    """
    token_data = verify_token(credentials.credentials)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="טוקן לא תקין או פג תוקף",
        )

    # ולידציה שהתחנה עדיין פעילה
    station_service = StationService(db)
    station = await station_service.get_station(token_data.station_id)
    if not station:
        logger.warning(
            "Panel access denied — station inactive",
            extra_data={"user_id": token_data.user_id, "station_id": token_data.station_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="התחנה לא פעילה",
        )

    return token_data
