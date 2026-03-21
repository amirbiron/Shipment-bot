"""
אימות אדמין דרך Telegram Login Widget

זרימה:
1. אדמין לוחץ על כפתור טלגרם ב-Swagger UI
2. נתוני האימות מאומתים מול הטוקן של הבוט (HMAC-SHA256)
3. המערכת מוודאת שהמשתמש הוא אדמין פעיל
4. מונפק JWT token עם role=admin
"""
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    create_access_token,
    create_refresh_token,
    verify_telegram_login_data,
)
from app.core.logging import get_logger
from app.db.database import get_db
from app.db.models.user import User, UserRole

logger = get_logger(__name__)

router = APIRouter()

# station_id דמה לאדמין — אדמין לא שייך לתחנה
_ADMIN_STATION_ID = 0


# ==================== סכמות ====================


class TelegramLoginRequest(BaseModel):
    """נתוני אימות מ-Telegram Login Widget"""
    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int
    hash: str

    @field_validator("auth_date")
    @classmethod
    def validate_auth_date(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("auth_date חייב להיות חיובי")
        return v


class AdminTokenResponse(BaseModel):
    """תגובת התחברות אדמין"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: int
    username: str


# ==================== Endpoints ====================


@router.post(
    "/telegram-login",
    response_model=AdminTokenResponse,
    summary="כניסת אדמין דרך Telegram Login Widget",
    description=(
        "אימות אדמין באמצעות Telegram Login Widget. "
        "הנתונים מאומתים מול הטוקן של הבוט (HMAC-SHA256). "
        "רק משתמשים עם תפקיד admin יכולים להתחבר."
    ),
    responses={
        200: {"description": "התחברות הצליחה"},
        401: {"description": "אימות טלגרם נכשל או המשתמש לא מורשה"},
    },
    tags=["Admin - אימות"],
)
async def admin_telegram_login(
    data: TelegramLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AdminTokenResponse:
    """כניסת אדמין דרך Telegram Login Widget — אימות HMAC-SHA256 + הנפקת JWT"""
    # אימות נתוני טלגרם (hash + תוקף)
    auth_data = data.model_dump(exclude_none=True)
    if not verify_telegram_login_data(auth_data):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="אימות טלגרם נכשל — נתונים לא תקינים או פגי תוקף",
        )

    # חיפוש משתמש לפי telegram_chat_id
    telegram_id = str(data.id)
    result = await db.execute(
        select(User).where(User.telegram_chat_id == telegram_id)
    )
    user = result.scalar_one_or_none()

    # בדיקת הרשאת אדמין — תשובה אחידה לכל כשלון
    if not user or not user.is_active or user.role != UserRole.ADMIN:
        logger.info("Admin Telegram Login — משתמש לא מורשה", extra_data={
            "telegram_id": telegram_id,
            "user_found": user is not None,
            "is_active": user.is_active if user else None,
            "role": str(user.role) if user else None,
        })
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="המשתמש לא רשום כאדמין או שהחשבון אינו פעיל",
        )

    # הנפקת JWT + refresh token עם role=admin
    token = create_access_token(
        user_id=user.id,
        station_id=_ADMIN_STATION_ID,
        role=user.role.value,
    )
    refresh = await create_refresh_token(
        user_id=user.id,
        station_id=_ADMIN_STATION_ID,
        role=user.role.value,
    )

    display_name = user.full_name or user.name or data.first_name or "אדמין"

    logger.info(
        "Admin Telegram Login — כניסה הצליחה",
        extra_data={"user_id": user.id, "telegram_id": telegram_id},
    )

    return AdminTokenResponse(
        access_token=token,
        refresh_token=refresh,
        user_id=user.id,
        username=display_name,
    )
