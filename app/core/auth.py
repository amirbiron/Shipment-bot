"""
אימות JWT לפאנל ווב — יצירת ואימות טוקנים + OTP

זרימת הכניסה:
1. בעל תחנה מבקש OTP (דרך /api/panel/auth/request-otp)
2. OTP נשמר ב-Redis עם TTL
3. הקוד נשלח אליו דרך הבוט (Telegram/WhatsApp)
4. בעל התחנה מזין את הקוד בפאנל ומקבל JWT token
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt as pyjwt
from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

_OTP_KEY_PREFIX = "panel_otp"
_OTP_COOLDOWN_PREFIX = "panel_otp_cooldown"
_OTP_ATTEMPTS_PREFIX = "panel_otp_attempts"

# הגבלות OTP
OTP_MAX_ATTEMPTS = 5  # מקסימום ניסיונות אימות לכל משתמש
OTP_COOLDOWN_SECONDS = 60  # זמן המתנה בין בקשות OTP


class TokenPayload(BaseModel):
    """תוכן ה-JWT token"""
    user_id: int
    station_id: int
    role: str
    exp: int  # Unix timestamp — סטנדרט JWT


def create_access_token(user_id: int, station_id: int, role: str) -> str:
    """יצירת JWT token לפאנל"""
    if not settings.JWT_SECRET_KEY:
        raise ValueError("JWT_SECRET_KEY לא מוגדר — אי אפשר ליצור טוקן")
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "user_id": user_id,
        "station_id": station_id,
        "role": role,
        "exp": int(expire.timestamp()),
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


async def check_otp_cooldown(user_id: int) -> bool:
    """בדיקה אם המשתמש עדיין בזמן המתנה — True אם מותר לשלוח"""
    redis = await get_redis()
    cooldown_key = f"{_OTP_COOLDOWN_PREFIX}:{user_id}"
    existing = await redis.get(cooldown_key)
    return existing is None


async def store_otp(user_id: int, otp: str) -> None:
    """שמירת OTP ב-Redis עם TTL + cooldown + איפוס מונה ניסיונות"""
    redis = await get_redis()
    key = f"{_OTP_KEY_PREFIX}:{user_id}"
    cooldown_key = f"{_OTP_COOLDOWN_PREFIX}:{user_id}"
    attempts_key = f"{_OTP_ATTEMPTS_PREFIX}:{user_id}"
    await redis.setex(key, settings.OTP_EXPIRE_SECONDS, otp)
    await redis.setex(cooldown_key, OTP_COOLDOWN_SECONDS, "1")
    # איפוס מונה ניסיונות — בקשת OTP חדש פותחת חלון ניסיונות מחדש
    await redis.delete(attempts_key)
    logger.info("OTP stored", extra_data={"user_id": user_id})


async def check_otp_attempts(user_id: int) -> bool:
    """בדיקה אם נותרו ניסיונות אימות — True אם לא חרג מהמקסימום"""
    redis = await get_redis()
    attempts_key = f"{_OTP_ATTEMPTS_PREFIX}:{user_id}"
    count = await redis.get(attempts_key)
    if count is not None and int(count) >= OTP_MAX_ATTEMPTS:
        return False
    return True


async def _increment_and_check_otp_attempts(user_id: int) -> bool:
    """הגדלה אטומית של מונה ניסיונות + בדיקת מגבלה — True אם עדיין מותר"""
    redis = await get_redis()
    attempts_key = f"{_OTP_ATTEMPTS_PREFIX}:{user_id}"
    # INCR אטומי — מונע race condition בבקשות מקבילות
    new_count = await redis.incr(attempts_key)
    if new_count == 1:
        # מפתח חדש — מגדירים TTL כזמן חיי ה-OTP
        await redis.expire(attempts_key, settings.OTP_EXPIRE_SECONDS)
    return new_count <= OTP_MAX_ATTEMPTS


async def verify_otp(user_id: int, otp: str) -> bool:
    """אימות OTP — מוחק לאחר שימוש (one-time), עם מגבלת ניסיונות אטומית"""
    # הגדלה אטומית + בדיקת מגבלה — פעולה אחת, ללא TOCTOU
    allowed = await _increment_and_check_otp_attempts(user_id)
    if not allowed:
        logger.warning("OTP max attempts exceeded", extra_data={"user_id": user_id})
        return False

    redis = await get_redis()
    key = f"{_OTP_KEY_PREFIX}:{user_id}"
    stored = await redis.get(key)
    if stored and stored == otp:
        await redis.delete(key)
        # מאפס ניסיונות אחרי הצלחה
        attempts_key = f"{_OTP_ATTEMPTS_PREFIX}:{user_id}"
        await redis.delete(attempts_key)
        logger.info("OTP verified successfully", extra_data={"user_id": user_id})
        return True

    logger.warning("OTP verification failed", extra_data={"user_id": user_id})
    return False
