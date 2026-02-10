"""
Redis Client — async singleton לשימוש כללי.

משתמש ב-REDIS_URL מהקונפיגורציה (ברירת מחדל: redis://localhost:6379/0).
"""
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """מחזיר Redis client singleton (async, connection pool)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
        logger.info("Redis client initialized", extra_data={"url": settings.REDIS_URL})
    return _redis_client


async def close_redis() -> None:
    """סגירת חיבור Redis — לקרוא ב-app shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed")
