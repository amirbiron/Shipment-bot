"""
שירות בדיקת בריאות — בדיקות תלויות (DB, Redis, WhatsApp Gateway, Celery).

מספק שתי רמות בדיקה:
- liveness: האם התהליך חי (ללא בדיקת תלויות)
- readiness: בדיקה מקיפה של כל התלויות החיצוניות
"""
from typing import Any

import httpx
import redis.asyncio as aioredis
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.db.database import AsyncSessionLocal

logger = get_logger(__name__)

# סטטוסים אפשריים לתשובת readiness
_STATUS_HEALTHY = "healthy"
_STATUS_DEGRADED = "degraded"

_CHECK_OK = "ok"

# הודעות שגיאה מסוננות — ללא חשיפת פרטי תשתית
_ERROR_DB = "error: db_unavailable"
_ERROR_REDIS = "error: redis_unavailable"
_ERROR_WHATSAPP = "error: whatsapp_unavailable"
_ERROR_WHATSAPP_DISCONNECTED = "error: whatsapp_disconnected"
_ERROR_CELERY = "error: celery_unavailable"


async def _check_db() -> str:
    """בדיקת חיבור למסד הנתונים באמצעות שאילתה קלה."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return _CHECK_OK
    except Exception as e:
        logger.warning("בדיקת בריאות DB נכשלה", extra_data={"error": str(e)})
        return _ERROR_DB


async def _check_redis() -> str:
    """בדיקת חיבור ל-Redis באמצעות PING."""
    try:
        client = await get_redis()
        await client.ping()
        return _CHECK_OK
    except Exception as e:
        logger.warning("בדיקת בריאות Redis נכשלה", extra_data={"error": str(e)})
        return _ERROR_REDIS


async def _check_whatsapp_gateway() -> str:
    """בדיקת זמינות WhatsApp Gateway באמצעות בקשת HTTP וולידציית מצב חיבור."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.WHATSAPP_GATEWAY_URL}/health")
            if response.status_code != 200:
                logger.warning(
                    "WhatsApp Gateway החזיר סטטוס לא תקין",
                    extra_data={"status_code": response.status_code},
                )
                return _ERROR_WHATSAPP
            # ה-Gateway מחזיר {"status": "ok", "connected": true/false}.
            # סטטוס 200 לבד לא מעיד על חיבור תקין — חובה לבדוק את שדה connected.
            data = response.json()
            if not data.get("connected"):
                logger.warning(
                    "WhatsApp Gateway פעיל אך לא מחובר",
                    extra_data={"response": data},
                )
                return _ERROR_WHATSAPP_DISCONNECTED
            return _CHECK_OK
    except Exception as e:
        logger.warning(
            "בדיקת בריאות WhatsApp Gateway נכשלה",
            extra_data={"error": str(e)},
        )
        return _ERROR_WHATSAPP


async def _check_celery() -> str:
    """בדיקת זמינות Celery workers באמצעות ping ל-broker (Redis)."""
    try:
        client = aioredis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
        try:
            await client.ping()
            return _CHECK_OK
        finally:
            await client.aclose()
    except Exception as e:
        logger.warning("בדיקת בריאות Celery נכשלה", extra_data={"error": str(e)})
        return _ERROR_CELERY


async def check_readiness() -> dict[str, Any]:
    """
    בדיקת מוכנות מקיפה — בודק את כל התלויות החיצוניות.

    מחזיר dict עם סטטוס כללי ופירוט לכל תלות:
    - status: "healthy" אם הכל תקין, "degraded" אם יש בעיה באחת התלויות
    - db / redis / whatsapp_gateway / celery: "ok" או "error: ..."
    """
    db_status = await _check_db()
    redis_status = await _check_redis()
    whatsapp_status = await _check_whatsapp_gateway()
    celery_status = await _check_celery()

    checks = {
        "db": db_status,
        "redis": redis_status,
        "whatsapp_gateway": whatsapp_status,
        "celery": celery_status,
    }

    all_ok = all(v == _CHECK_OK for v in checks.values())
    overall_status = _STATUS_HEALTHY if all_ok else _STATUS_DEGRADED

    if not all_ok:
        logger.warning(
            "בדיקת מוכנות — המערכת במצב degraded",
            extra_data=checks,
        )

    return {"status": overall_status, **checks}
