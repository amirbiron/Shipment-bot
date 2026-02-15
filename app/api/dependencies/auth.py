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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_token, TokenPayload
from app.db.database import get_db
from app.db.models.user import User
from app.domain.services.station_service import StationService
from app.core.logging import get_logger

logger = get_logger(__name__)

security = HTTPBearer()


async def validate_station_owner(
    token_data: TokenPayload,
    db: AsyncSession,
) -> None:
    """
    ולידציה מלאה שהמשתמש הוא בעל תחנה פעילה.

    בודק:
    1. תפקיד station_owner בטוקן
    2. המשתמש עדיין פעיל ב-DB
    3. התחנה עדיין פעילה
    4. המשתמש עדיין בעלים של התחנה

    זורק HTTPException 403 אם אחד מהתנאים לא מתקיים.
    """
    # ולידציה שהטוקן שייך לבעל תחנה
    if token_data.role != "station_owner":
        logger.error(
            "Panel access denied — wrong role in token",
            extra_data={"user_id": token_data.user_id, "role": token_data.role},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="אין הרשאה — טוקן לא מתאים לפאנל",
        )

    # ולידציה שהמשתמש עדיין פעיל
    user_result = await db.execute(
        select(User).where(User.id == token_data.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        logger.error(
            "Panel access denied — user inactive",
            extra_data={
                "user_id": token_data.user_id,
                "user_found": user is not None,
                "is_active": user.is_active if user else None,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="חשבון המשתמש אינו פעיל",
        )

    # ולידציה שהתחנה עדיין פעילה
    station_service = StationService(db)
    station = await station_service.get_station(token_data.station_id)
    if not station:
        logger.error(
            "Panel access denied — station inactive or not found",
            extra_data={
                "user_id": token_data.user_id,
                "station_id": token_data.station_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="התחנה לא פעילה",
        )

    # ולידציה שהמשתמש עדיין בעלים של התחנה
    is_owner = await station_service.is_owner_of_station(
        token_data.user_id, token_data.station_id
    )
    if not is_owner:
        logger.error(
            "Panel access denied — ownership mismatch",
            extra_data={
                "user_id": token_data.user_id,
                "station_id": token_data.station_id,
                "station_name": station.name,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="אין הרשאה — הבעלות על התחנה השתנתה",
        )


async def get_current_station_owner(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> TokenPayload:
    """
    אימות JWT ווידוא שהמשתמש הוא בעל תחנה פעילה.

    מחזיר TokenPayload עם user_id, station_id, role.
    זורק 401 אם הטוקן לא תקין, 403 אם התחנה לא פעילה או הבעלות השתנתה.
    """
    token_data = verify_token(credentials.credentials)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="טוקן לא תקין או פג תוקף",
        )

    await validate_station_owner(token_data, db)
    return token_data
