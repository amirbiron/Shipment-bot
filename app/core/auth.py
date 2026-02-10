"""
אימות JWT לפאנל ווב — יצירת ואימות טוקנים + OTP

זרימת הכניסה:
1. בעל תחנה מבקש OTP (דרך /api/panel/auth/request-otp)
2. OTP נשמר ב-Redis עם TTL
3. הקוד נשלח אליו דרך הבוט (Telegram/WhatsApp)
4. בעל התחנה מזין את הקוד בפאנל ומקבל JWT token
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional

import jwt as pyjwt
from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

_OTP_KEY_PREFIX = "panel_otp"


class TokenPayload(BaseModel):
    """תוכן ה-JWT token"""
    user_id: int
    station_id: int
    role: str
    exp: datetime


def create_access_token(user_id: int, station_id: int, role: str) -> str:
    """יצירת JWT token לפאנל"""
    if not settings.JWT_SECRET_KEY:
        raise ValueError("JWT_SECRET_KEY לא מוגדר — אי אפשר ליצור טוקן")
    expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "user_id": user_id,
        "station_id": station_id,
        "role": role,
        "exp": expire,
    }
    encoded = pyjwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    logger.info(
        "JWT token created",
        extra_data={"user_id": user_id, "station_id": station_id},
    )
    return encoded


def verify_token(token: str) -> Optional[TokenPayload]:
    """אימות JWT token — מחזיר None אם לא תקין או פג תוקף"""
    if not settings.JWT_SECRET_KEY:
        logger.error("JWT_SECRET_KEY ריק — טוקנים לא יאומתו")
        return None
    try:
        payload = pyjwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return TokenPayload(**payload)
    except pyjwt.InvalidTokenError:
        logger.warning("JWT token invalid or expired")
        return None
    except (KeyError, ValueError) as e:
        logger.warning("JWT payload malformed", extra_data={"error": str(e)})
        return None


def generate_otp() -> str:
    """יצירת קוד OTP בטוח — 6 ספרות"""
    return f"{secrets.randbelow(1000000):06d}"


async def store_otp(user_id: int, otp: str) -> None:
    """שמירת OTP ב-Redis עם TTL"""
    redis = await get_redis()
    key = f"{_OTP_KEY_PREFIX}:{user_id}"
    await redis.setex(key, settings.OTP_EXPIRE_SECONDS, otp)
    logger.info("OTP stored", extra_data={"user_id": user_id})


async def verify_otp(user_id: int, otp: str) -> bool:
    """אימות OTP — מוחק לאחר שימוש (one-time)"""
    redis = await get_redis()
    key = f"{_OTP_KEY_PREFIX}:{user_id}"
    stored = await redis.get(key)
    if stored and stored == otp:
        await redis.delete(key)
        logger.info("OTP verified successfully", extra_data={"user_id": user_id})
        return True
    logger.warning("OTP verification failed", extra_data={"user_id": user_id})
    return False
