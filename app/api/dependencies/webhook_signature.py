"""
אימות חתימת webhook מקיף — מונע הזרקת הודעות מזויפות.

פיצ'ר 4: תמיכה בשלושה ספקים:
- Telegram: X-Telegram-Bot-Api-Secret-Token (כבר קיים ב-webhook_auth.py)
- WhatsApp Cloud API: X-Hub-Signature-256 (HMAC-SHA256)
- WPPConnect: X-Webhook-Signature (HMAC-SHA256)

חסימת IP מבוססת Redis — עובדת נכון גם עם מספר workers/pods.
Fallback לזיכרון מקומי אם Redis לא זמין.
"""
import hashlib
import hmac
import time
from collections import defaultdict

from fastapi import Request, HTTPException, status

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# מפתחות Redis לחסימת IP
_REDIS_BLOCK_KEY_PREFIX = "webhook:blocked:"
_REDIS_ATTEMPTS_KEY_PREFIX = "webhook:attempts:"

# Fallback לזיכרון מקומי — משמש רק כש-Redis לא זמין
_failed_attempts: dict[str, list[float]] = defaultdict(list)
_blocked_ips: dict[str, float] = {}


async def _get_redis_safe() -> "aioredis.Redis | None":
    """מחזיר Redis client או None אם לא זמין."""
    try:
        from app.core.redis_client import get_redis
        return await get_redis()
    except Exception:
        return None


def _get_trusted_proxy_ips() -> set[str]:
    """רשימת כתובות IP של reverse proxies מהימנים."""
    raw = settings.TRUSTED_PROXY_IPS
    if not raw:
        return set()
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def _get_client_ip(request: Request) -> str:
    """חילוץ IP של הלקוח — מכבד X-Forwarded-For רק מ-proxy מהימן.

    אם הבקשה מגיעה מ-IP שנמצא ב-TRUSTED_PROXY_IPS, סומכים על
    X-Forwarded-For ולוקחים את ה-IP הראשון ברשימה.
    אחרת משתמשים ב-client IP ישירות — מונע עקיפת חסימת IP.
    """
    direct_ip = request.client.host if request.client else "unknown"

    trusted_proxies = _get_trusted_proxy_ips()
    if not trusted_proxies:
        # אין proxies מוגדרים — לא סומכים על X-Forwarded-For
        return direct_ip

    if direct_ip in trusted_proxies:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

    return direct_ip


async def _is_ip_blocked(client_ip: str) -> bool:
    """בדיקה האם IP חסום — Redis עם fallback לזיכרון מקומי."""
    redis = await _get_redis_safe()
    if redis is not None:
        try:
            blocked = await redis.exists(f"{_REDIS_BLOCK_KEY_PREFIX}{client_ip}")
            return bool(blocked)
        except Exception as e:
            logger.warning(
                "כשלון בבדיקת חסימת IP ב-Redis — fallback לזיכרון מקומי",
                extra_data={"error": str(e)},
            )

    # Fallback — זיכרון מקומי
    expiry = _blocked_ips.get(client_ip)
    if expiry is None:
        return False
    if time.time() > expiry:
        del _blocked_ips[client_ip]
        _failed_attempts.pop(client_ip, None)
        return False
    return True


async def _record_failed_attempt(client_ip: str) -> None:
    """רישום ניסיון אימות כושל וחסימת IP אם חרג מהמכסה.

    משתמש ב-Redis sorted set לספירת ניסיונות בחלון זמן,
    עובד נכון גם עם מספר workers.
    """
    window = settings.WEBHOOK_SIGNATURE_BLOCK_DURATION_SECONDS
    threshold = settings.WEBHOOK_SIGNATURE_BLOCK_AFTER

    redis = await _get_redis_safe()
    if redis is not None:
        try:
            now = time.time()
            attempts_key = f"{_REDIS_ATTEMPTS_KEY_PREFIX}{client_ip}"

            # ניקוי ניסיונות ישנים + הוספת החדש באופן אטומי
            pipe = redis.pipeline()
            pipe.zremrangebyscore(attempts_key, "-inf", now - window)
            pipe.zadd(attempts_key, {str(now): now})
            pipe.zcard(attempts_key)
            pipe.expire(attempts_key, int(window) + 1)
            results = await pipe.execute()

            attempt_count = results[2]

            if attempt_count >= threshold:
                block_key = f"{_REDIS_BLOCK_KEY_PREFIX}{client_ip}"
                await redis.setex(block_key, int(window), "1")
                logger.warning(
                    "IP חסום אוטומטית אחרי ניסיונות אימות webhook כושלים",
                    extra_data={
                        "client_ip": client_ip,
                        "failed_attempts": attempt_count,
                        "block_duration_seconds": window,
                    },
                )
            return
        except Exception as e:
            logger.warning(
                "כשלון ברישום ניסיון כושל ב-Redis — fallback לזיכרון מקומי",
                extra_data={"error": str(e)},
            )

    # Fallback — זיכרון מקומי
    now = time.time()
    _failed_attempts[client_ip] = [
        ts for ts in _failed_attempts[client_ip] if now - ts < window
    ]
    _failed_attempts[client_ip].append(now)

    attempt_count = len(_failed_attempts[client_ip])

    if attempt_count >= threshold:
        _blocked_ips[client_ip] = now + window
        logger.warning(
            "IP חסום אוטומטית אחרי ניסיונות אימות webhook כושלים (fallback מקומי)",
            extra_data={
                "client_ip": client_ip,
                "failed_attempts": attempt_count,
                "block_duration_seconds": window,
            },
        )


def verify_wppconnect_signature(body: bytes, signature_header: str | None) -> bool:
    """אימות חתימת HMAC-SHA256 של WPPConnect על ה-payload.

    WPPConnect שולח את החתימה בכותרת X-Webhook-Signature בפורמט sha256=<hex>.
    """
    secret = settings.WPPCONNECT_WEBHOOK_SECRET
    if not secret:
        # סוד לא מוגדר — אין אימות (אזהרה נרשמת ב-startup)
        return True

    if not signature_header:
        return False

    # תמיכה בפורמטים: "sha256=<hex>" ו-"<hex>" ישירות
    if signature_header.startswith("sha256="):
        provided_sig = signature_header[7:]
    else:
        provided_sig = signature_header

    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(provided_sig, expected)


async def verify_webhook_signature(
    request: Request, provider: str
) -> None:
    """אימות חתימת webhook לפי ספק — עם חסימת IP אוטומטית.

    Args:
        request: אובייקט הבקשה
        provider: שם הספק ("wppconnect" / "whatsapp_cloud" / "telegram")

    Raises:
        HTTPException: 403 אם האימות נכשל, 429 אם IP חסום
    """
    client_ip = _get_client_ip(request)

    # בדיקת חסימת IP
    if await _is_ip_blocked(client_ip):
        logger.warning(
            "בקשת webhook מ-IP חסום",
            extra_data={"client_ip": client_ip, "provider": provider},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="IP חסום זמנית עקב ניסיונות אימות כושלים חוזרים",
        )

    if provider == "wppconnect":
        secret = settings.WPPCONNECT_WEBHOOK_SECRET
        if not secret:
            # אימות לא מוגדר — מדלג
            return

        body = await request.body()
        signature = request.headers.get("X-Webhook-Signature")

        if not verify_wppconnect_signature(body, signature):
            await _record_failed_attempt(client_ip)
            logger.warning(
                "WPPConnect webhook: חתימה לא תקינה",
                extra_data={"client_ip": client_ip},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="חתימת webhook לא תקינה",
            )

    elif provider == "whatsapp_cloud":
        # כבר מטופל ב-whatsapp_cloud.py — כאן רק בדיקת IP blocking
        pass

    elif provider == "telegram":
        # כבר מטופל ב-webhook_auth.py — כאן רק בדיקת IP blocking
        pass


async def require_wppconnect_signature(request: Request) -> None:
    """Dependency לאימות חתימת WPPConnect — רץ לפני פרסור ה-body של FastAPI.

    שימוש כ-Depends() כדי לוודא שבקשות לא מאומתות נדחות
    לפני שה-JSON מפורסר ונחשף מבנה ה-schema (מונע 422 על חתימה שגויה).

    Raises:
        HTTPException: 403 אם חתימה לא תקינה, 429 אם IP חסום
    """
    client_ip = _get_client_ip(request)

    if await _is_ip_blocked(client_ip):
        logger.warning(
            "בקשת webhook מ-IP חסום",
            extra_data={"client_ip": client_ip, "provider": "wppconnect"},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="IP חסום זמנית עקב ניסיונות אימות כושלים חוזרים",
        )

    secret = settings.WPPCONNECT_WEBHOOK_SECRET
    if not secret:
        # אימות לא מוגדר — מדלג
        return

    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature")

    if not verify_wppconnect_signature(body, signature):
        await _record_failed_attempt(client_ip)
        logger.warning(
            "WPPConnect webhook: חתימה לא תקינה",
            extra_data={"client_ip": client_ip},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="חתימת webhook לא תקינה",
        )


async def get_blocked_ips() -> dict[str, float]:
    """שליפת רשימת IP חסומים — לצורך ניטור."""
    redis = await _get_redis_safe()
    if redis is not None:
        try:
            # סריקת כל מפתחות חסימה ב-Redis
            blocked: dict[str, float] = {}
            cursor = "0"
            while True:
                cursor, keys = await redis.scan(
                    cursor=cursor,
                    match=f"{_REDIS_BLOCK_KEY_PREFIX}*",
                    count=100,
                )
                for key in keys:
                    ttl = await redis.ttl(key)
                    if ttl > 0:
                        ip = key.replace(_REDIS_BLOCK_KEY_PREFIX, "")
                        blocked[ip] = float(ttl)
                if cursor == "0" or cursor == 0:
                    break
            return blocked
        except Exception as e:
            logger.warning(
                "כשלון בשליפת IP חסומים מ-Redis",
                extra_data={"error": str(e)},
            )

    # Fallback — זיכרון מקומי
    now = time.time()
    expired = [ip for ip, expiry in _blocked_ips.items() if now > expiry]
    for ip in expired:
        del _blocked_ips[ip]
        _failed_attempts.pop(ip, None)

    return {
        ip: round(expiry - now, 1)
        for ip, expiry in _blocked_ips.items()
    }


async def get_failed_attempt_counts() -> dict[str, int]:
    """מספר ניסיונות כושלים לכל IP — לצורך ניטור."""
    redis = await _get_redis_safe()
    if redis is not None:
        try:
            counts: dict[str, int] = {}
            cursor = "0"
            while True:
                cursor, keys = await redis.scan(
                    cursor=cursor,
                    match=f"{_REDIS_ATTEMPTS_KEY_PREFIX}*",
                    count=100,
                )
                for key in keys:
                    count = await redis.zcard(key)
                    if count > 0:
                        ip = key.replace(_REDIS_ATTEMPTS_KEY_PREFIX, "")
                        counts[ip] = count
                if cursor == "0" or cursor == 0:
                    break
            return counts
        except Exception as e:
            logger.warning(
                "כשלון בשליפת ניסיונות כושלים מ-Redis",
                extra_data={"error": str(e)},
            )

    # Fallback — זיכרון מקומי
    return {ip: len(attempts) for ip, attempts in _failed_attempts.items() if attempts}
