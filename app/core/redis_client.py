"""
Redis Client — async singleton לשימוש כללי.

משתמש ב-REDIS_URL מהקונפיגורציה (ברירת מחדל: redis://localhost:6379/0).
"""
import asyncio
from urllib.parse import urlparse

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: aioredis.Redis | None = None
_init_lock = asyncio.Lock()


def _mask_redis_url(url: str) -> str:
    """מסתיר סיסמה מ-REDIS_URL ללוגים (redis://:****@host:6379)."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            return url.replace(f":{parsed.password}@", ":****@")
        return url
    except Exception:
        return "redis://****"


async def get_redis() -> aioredis.Redis:
    """מחזיר Redis client singleton (async, connection pool)."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    async with _init_lock:
        # בדיקה חוזרת אחרי נעילה — ייתכן ש-request מקבילי כבר אתחל
        if _redis_client is not None:
            return _redis_client

        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
        await client.ping()
        _redis_client = client
        logger.info("Redis client initialized", extra_data={
            "url": _mask_redis_url(settings.REDIS_URL),
        })
    return _redis_client


async def close_redis() -> None:
    """סגירת חיבור Redis — לקרוא ב-app shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed")
