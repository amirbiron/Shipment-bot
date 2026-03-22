"""
PostHog Product Analytics — אתחול ומעקב אירועים

מספק:
- אתחול PostHog SDK עם הגדרות מהקונפיגורציה
- פונקציות עזר לשליחת אירועים וזיהוי משתמשים
- הגנת PII: מיסוך מספרי טלפון בנתוני אירועים
- כיבוי graceful בסגירת האפליקציה
"""

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# לייבא רק כשצריך — כדי לא לכשול אם posthog לא מותקן
_posthog_client: Any = None


def _scrub_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """מיסוך מספרי טלפון מנתוני אירועים — מאציל ל-_scrub_dict של sentry.py שמטפל בכל הטיפוסים"""
    from app.core.sentry import _scrub_dict

    return _scrub_dict(properties)


def init_posthog() -> None:
    """אתחול PostHog SDK — קריאה חד-פעמית בעלייה של FastAPI או Celery worker.

    אם POSTHOG_PROJECT_TOKEN ריק, PostHog לא יאותחל ואין תופעות לוואי.
    idempotent — קריאה חוזרת לא תיצור client נוסף.
    """
    global _posthog_client

    # הגנת idempotency — מונע יצירת client כפול ודליפת thread
    if _posthog_client is not None:
        return

    from app.core.config import settings

    if not settings.POSTHOG_PROJECT_TOKEN:
        logger.info("POSTHOG_PROJECT_TOKEN לא מוגדר — PostHog מושבת")
        return

    try:
        from posthog import Posthog

        _posthog_client = Posthog(
            project_api_key=settings.POSTHOG_PROJECT_TOKEN,
            host=settings.POSTHOG_HOST,
            debug=settings.DEBUG,
            # שליחת אירועים ב-batch — ביצועים טובים יותר
            on_error=_on_posthog_error,
        )

        logger.info(
            "PostHog אותחל בהצלחה",
            extra_data={"host": settings.POSTHOG_HOST},
        )
    except Exception as e:
        logger.error(
            "כשלון באתחול PostHog",
            extra_data={"error": str(e)},
            exc_info=True,
        )


def _on_posthog_error(error: Exception, items: list[Any]) -> None:
    """callback לשגיאות PostHog — לוג במקום בליעת שגיאות"""
    logger.error(
        "שגיאת PostHog בשליחת אירועים",
        extra_data={"error": str(error), "items_count": len(items)},
    )


def capture_event(
    distinct_id: str,
    event: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """שליחת אירוע ל-PostHog עם מיסוך PII.

    Args:
        distinct_id: מזהה ייחודי של המשתמש (user_id כ-string)
        event: שם האירוע (למשל "delivery_created", "courier_registered")
        properties: נתונים נוספים לאירוע
    """
    if _posthog_client is None:
        return

    try:
        safe_properties = _scrub_properties(properties) if properties else {}
        _posthog_client.capture(
            distinct_id=distinct_id,
            event=event,
            properties=safe_properties,
        )
    except Exception as e:
        logger.error(
            "כשלון בשליחת אירוע PostHog",
            extra_data={"event": event, "error": str(e)},
            exc_info=True,
        )


def identify_user(
    distinct_id: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """זיהוי משתמש ב-PostHog עם מאפיינים (role, platform וכו').

    משתמש ב-set() (PostHog SDK v6+) להגדרת מאפייני משתמש.

    Args:
        distinct_id: מזהה ייחודי של המשתמש (user_id כ-string)
        properties: מאפייני המשתמש (למשל role, platform)
    """
    if _posthog_client is None:
        return

    try:
        safe_properties = _scrub_properties(properties) if properties else {}
        _posthog_client.set(
            distinct_id=distinct_id,
            properties=safe_properties,
        )
    except Exception as e:
        logger.error(
            "כשלון בזיהוי משתמש PostHog",
            extra_data={"error": str(e)},
            exc_info=True,
        )


def shutdown_posthog() -> None:
    """כיבוי PostHog — שליחת אירועים שנותרו בתור לפני סגירה"""
    global _posthog_client

    if _posthog_client is None:
        return

    try:
        _posthog_client.shutdown()
        logger.info("PostHog נסגר בהצלחה")
    except Exception as e:
        logger.error(
            "שגיאה בסגירת PostHog",
            extra_data={"error": str(e)},
            exc_info=True,
        )
    finally:
        _posthog_client = None
