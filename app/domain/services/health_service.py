"""
שירות בדיקת בריאות — בדיקות תלויות (DB, Redis, WhatsApp Gateway, Celery).

מספק שלוש רמות בדיקה:
- liveness: האם התהליך חי (ללא בדיקת תלויות)
- readiness: בדיקה מקיפה של כל התלויות החיצוניות
- detailed: בדיקה מעמיקה עם זמני תגובה, מצב circuit breakers, מידע DB pool ו-uptime
"""
import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import httpx
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.db.database import AsyncSessionLocal

# זמן הפעלת התהליך — לחישוב uptime
_process_start_time = time.monotonic()

logger = get_logger(__name__)

# סטטוסים אפשריים לתשובת readiness
_STATUS_HEALTHY = "healthy"
_STATUS_DEGRADED = "degraded"
_STATUS_UNHEALTHY = "unhealthy"

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
    - status: "healthy" אם הכל תקין, "degraded" אם יש בעיה בתלות לא קריטית,
      "unhealthy" אם תלות קריטית לא זמינה
    - db / redis / whatsapp_gateway / celery: "ok" או "error: ..."

    תלויות קריטיות (DB, Redis, Celery) — כשל בהן מחזיר HTTP 503.
    תלויות לא קריטיות (WhatsApp Gateway) — כשל בהן מחזיר HTTP 200 עם status=degraded.
    זה מונע מצב שבו WhatsApp מושבת חוסם את כל התעבורה כולל טלגרם.
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

    # תלויות קריטיות — כשל בהן = המערכת לא יכולה לשרת בקשות
    critical_checks = {
        "db": db_status,
        "redis": redis_status,
        "celery": celery_status,
    }
    critical_ok = all(v == _CHECK_OK for v in critical_checks.values())
    all_ok = all(v == _CHECK_OK for v in checks.values())

    if not critical_ok:
        overall_status = _STATUS_UNHEALTHY
    elif not all_ok:
        overall_status = _STATUS_DEGRADED
    else:
        overall_status = _STATUS_HEALTHY

    if not all_ok:
        logger.warning(
            "בדיקת מוכנות — המערכת במצב %s",
            overall_status,
            extra_data=checks,
        )

    return {"status": overall_status, **checks}


# ============================================================================
# Detailed Health Check — מידע מורחב לדשבורד ניטור
# ============================================================================


async def _check_db_celery() -> str:
    """בדיקת חיבור ל-DB בטוחה ל-Celery — יוצרת engine חדש per-call."""
    try:
        task_engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            connect_args={"timeout": 10},
        )
        try:
            task_session_maker = async_sessionmaker(
                bind=task_engine, class_=AsyncSession, expire_on_commit=False
            )
            async with task_session_maker() as session:
                await session.execute(text("SELECT 1"))
            return _CHECK_OK
        finally:
            await task_engine.dispose()
    except Exception as e:
        logger.warning("בדיקת בריאות DB נכשלה (celery)", extra_data={"error": str(e)})
        return _ERROR_DB


async def _check_redis_celery() -> str:
    """בדיקת חיבור ל-Redis בטוחה ל-Celery — יוצרת חיבור חדש per-call."""
    try:
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await client.ping()
            return _CHECK_OK
        finally:
            await client.aclose()
    except Exception as e:
        logger.warning("בדיקת בריאות Redis נכשלה (celery)", extra_data={"error": str(e)})
        return _ERROR_REDIS


async def _timed_check(check_fn: Callable[[], Coroutine[Any, Any, str]]) -> dict[str, Any]:
    """הרצת פונקציית בדיקה עם מדידת זמן תגובה (ms)."""
    start = time.monotonic()
    try:
        result = await check_fn()
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": result, "response_time_ms": elapsed_ms}
    except Exception as e:
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        logger.warning("בדיקה מפורטת נכשלה", extra_data={"error": str(e)})
        return {"status": f"error: {type(e).__name__}", "response_time_ms": elapsed_ms}


def _get_circuit_breakers_status() -> list[dict[str, Any]]:
    """שליפת מצב כל ה-circuit breakers הרשומים."""
    from app.core.circuit_breaker import (
        get_telegram_circuit_breaker,
        get_whatsapp_circuit_breaker,
        get_whatsapp_admin_circuit_breaker,
        get_whatsapp_cloud_circuit_breaker,
    )

    # אתחול כל ה-circuit breakers הידועים כדי שיהיו ב-_instances
    known_breakers = [
        get_telegram_circuit_breaker(),
        get_whatsapp_circuit_breaker(),
        get_whatsapp_admin_circuit_breaker(),
        get_whatsapp_cloud_circuit_breaker(),
    ]

    result = []
    for cb in known_breakers:
        result.append({
            "service": cb.service_name,
            "state": cb.state.value,
            "failure_count": cb._state.failure_count,
            "retry_after_seconds": round(cb.get_retry_after(), 1),
        })
    return result


def _get_db_pool_info() -> dict[str, Any]:
    """שליפת מידע על connection pool של מסד הנתונים."""
    from app.db.database import engine

    pool = engine.pool
    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }


async def check_detailed(*, celery_mode: bool = False) -> dict[str, Any]:
    """
    בדיקת בריאות מפורטת — מחזיר מידע מורחב על כל הרכיבים.

    כולל:
    - סטטוס כל תלות + זמן תגובה (ms)
    - מצב כל circuit breakers
    - מידע על DB connection pool
    - uptime של התהליך
    - חותמת זמן של הבדיקה

    Args:
        celery_mode: כשהפונקציה רצה מתוך Celery task, משתמש ב-engine
                     וחיבור Redis חדשים per-call כדי למנוע event loop mismatch.
    """
    # בחירת פונקציות בדיקה — Celery יוצר event loop חדש per-task,
    # לכן חייבים engine וחיבור Redis חדשים (לא ה-singletons של המודול)
    db_fn = _check_db_celery if celery_mode else _check_db
    redis_fn = _check_redis_celery if celery_mode else _check_redis

    # הרצת בדיקות במקביל עם מדידת זמנים
    db_result, redis_result, whatsapp_result, celery_result = await asyncio.gather(
        _timed_check(db_fn),
        _timed_check(redis_fn),
        _timed_check(_check_whatsapp_gateway),
        _timed_check(_check_celery),
    )

    components = {
        "db": db_result,
        "redis": redis_result,
        "whatsapp_gateway": whatsapp_result,
        "celery": celery_result,
    }

    # חישוב סטטוס כללי
    critical_ok = all(
        components[k]["status"] == _CHECK_OK
        for k in ("db", "redis", "celery")
    )
    all_ok = all(c["status"] == _CHECK_OK for c in components.values())

    if not critical_ok:
        overall_status = _STATUS_UNHEALTHY
    elif not all_ok:
        overall_status = _STATUS_DEGRADED
    else:
        overall_status = _STATUS_HEALTHY

    # מצב circuit breakers
    try:
        circuit_breakers = _get_circuit_breakers_status()
    except Exception as e:
        logger.warning("כשלון בשליפת מצב circuit breakers", extra_data={"error": str(e)})
        circuit_breakers = []

    # מידע DB pool — רלוונטי רק ל-web process; ב-Celery ה-engine המודולרי לא בשימוש
    if celery_mode:
        db_pool = {"note": "not_applicable_in_celery_mode"}
    else:
        try:
            db_pool = _get_db_pool_info()
        except Exception as e:
            logger.warning("כשלון בשליפת מידע DB pool", extra_data={"error": str(e)})
            db_pool = {"error": "unavailable"}

    # uptime
    uptime_seconds = round(time.monotonic() - _process_start_time, 1)

    result = {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": uptime_seconds,
        "components": components,
        "circuit_breakers": circuit_breakers,
        "db_pool": db_pool,
    }

    if not all_ok:
        logger.warning(
            "בדיקה מפורטת — המערכת במצב %s",
            overall_status,
            extra_data={"status": overall_status},
        )

    return result
