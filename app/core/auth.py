"""
אימות JWT לפאנל ווב — יצירת ואימות טוקנים + OTP + refresh tokens

זרימת הכניסה:
1. בעל תחנה מבקש OTP (דרך /api/panel/auth/request-otp)
2. OTP נשמר ב-Redis עם TTL
3. הקוד נשלח אליו דרך הבוט (Telegram/WhatsApp)
4. בעל התחנה מזין את הקוד בפאנל ומקבל access token + refresh token
5. כש-access token פג — הלקוח שולח refresh token ומקבל access חדש
"""
import json
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
_OTP_PHONE_COOLDOWN_PREFIX = "panel_otp_phone_cooldown"
_OTP_ATTEMPTS_PREFIX = "panel_otp_attempts"
_REFRESH_TOKEN_PREFIX = "panel_refresh"

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


async def create_refresh_token(user_id: int, station_id: int, role: str) -> str:
    """יצירת refresh token — מחרוזת אקראית שנשמרת ב-Redis עם TTL ארוך.

    ה-refresh token עצמו הוא opaque (לא JWT) — המידע נשמר ב-Redis.
    יתרון: ניתן לבטל טוקן מיד ע\"י מחיקת המפתח ב-Redis.
    """
    token = secrets.token_urlsafe(48)
    redis = await get_redis()
    key = f"{_REFRESH_TOKEN_PREFIX}:{token}"
    ttl_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
    # שמירת המידע הנדרש להנפקת access token חדש
    payload = json.dumps({
        "user_id": user_id,
        "station_id": station_id,
        "role": role,
    })
    await redis.setex(key, ttl_seconds, payload)
    logger.info(
        "Refresh token created",
        extra_data={"user_id": user_id, "station_id": station_id},
    )
    return token


async def verify_refresh_token(token: str) -> Optional[TokenPayload]:
    """אימות refresh token — שולף ומוחק אטומית (one-time rotation).

    משתמש ב-GETDEL — פעולה אטומית אחת ב-Redis ששולפת ומוחקת בו-זמנית.
    מונע race condition שבו שני requests מקבילים שולפים את אותו טוקן לפני המחיקה.
    """
    redis = await get_redis()
    key = f"{_REFRESH_TOKEN_PREFIX}:{token}"
    # GETDEL אטומי — שולף ומוחק בפעולה אחת, מונע שימוש כפול
    stored = await redis.getdel(key)
    if not stored:
        logger.warning("Refresh token not found or expired")
        return None

    try:
        data = json.loads(stored)
        return TokenPayload(
            user_id=data["user_id"],
            station_id=data["station_id"],
            role=data["role"],
            exp=0,  # לא רלוונטי ל-refresh token
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Refresh token payload malformed", extra_data={"error": str(e)})
        return None


async def revoke_refresh_tokens_for_user(user_id: int, station_id: int) -> None:
    """ביטול כל ה-refresh tokens של משתמש לתחנה ספציפית.

    הערה: Redis לא תומך ב-pattern scan יעיל. אם נדרש revoke מלא,
    ניתן לשמור רשימת tokens per user. כרגע הפונקציה זמינה לשימוש עתידי.
    """
    logger.info(
        "Refresh token revocation requested",
        extra_data={"user_id": user_id, "station_id": station_id},
    )


def generate_otp() -> str:
    """יצירת קוד OTP בטוח — 6 ספרות"""
    return f"{secrets.randbelow(1000000):06d}"


async def try_set_otp_cooldown_by_phone(phone: str) -> bool:
    """בדיקה + הגדרת cooldown אטומית — SET NX EX.
    מחזיר True אם מותר (cooldown הוגדר עכשיו), False אם כבר בזמן המתנה.
    מבוסס על מספר טלפון (לא user_id) כדי למנוע enumeration."""
    redis = await get_redis()
    key = f"{_OTP_PHONE_COOLDOWN_PREFIX}:{phone}"
    # פעולה אטומית — SET רק אם המפתח לא קיים (NX) עם תפוגה (EX)
    result = await redis.set(key, "1", nx=True, ex=OTP_COOLDOWN_SECONDS)
    return result is not None


async def store_otp(user_id: int, otp: str) -> None:
    """שמירת OTP ב-Redis עם TTL + איפוס מונה ניסיונות"""
    redis = await get_redis()
    key = f"{_OTP_KEY_PREFIX}:{user_id}"
    attempts_key = f"{_OTP_ATTEMPTS_PREFIX}:{user_id}"
    await redis.setex(key, settings.OTP_EXPIRE_SECONDS, otp)
    # איפוס מונה ניסיונות — בקשת OTP חדש פותחת חלון ניסיונות מחדש
    await redis.delete(attempts_key)
    logger.info("OTP stored", extra_data={"user_id": user_id})


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


async def verify_otp(user_id: int, otp: str, consume: bool = True) -> bool:
    """אימות OTP — מוחק לאחר שימוש (one-time), עם מגבלת ניסיונות אטומית.

    Args:
        user_id: מזהה המשתמש
        otp: קוד OTP לאימות
        consume: האם למחוק את ה-OTP אחרי אימות מוצלח.
            False משמש למקרים כמו בחירת תחנה (station picker) — ה-OTP נשאר תקף לקריאה הבאה.
    """
    # הגדלה אטומית + בדיקת מגבלה — פעולה אחת, ללא TOCTOU
    allowed = await _increment_and_check_otp_attempts(user_id)
    if not allowed:
        logger.warning("OTP max attempts exceeded", extra_data={"user_id": user_id})
        return False

    redis = await get_redis()
    key = f"{_OTP_KEY_PREFIX}:{user_id}"
    stored = await redis.get(key)
    if stored and stored == otp:
        attempts_key = f"{_OTP_ATTEMPTS_PREFIX}:{user_id}"
        if consume:
            await redis.delete(key)
            # מאפס ניסיונות אחרי צריכה מוצלחת
            await redis.delete(attempts_key)
        else:
            # לא צורכים — DECR אטומי שמחזיר את המונה אחורה בלי למחוק TTL
            # (בניגוד ל-GET+SET שמוחק TTL ויוצר race condition)
            await redis.decr(attempts_key)
        logger.info("OTP verified successfully", extra_data={"user_id": user_id, "consumed": consume})
        return True

    logger.warning("OTP verification failed", extra_data={"user_id": user_id})
    return False
