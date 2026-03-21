"""
Sentry Error Tracking — אתחול וסינון PII

מספק:
- אתחול Sentry עם אינטגרציות FastAPI, Celery, SQLAlchemy
- סינון מספרי טלפון מ-events (before_send)
- העשרת events עם correlation ID
- הגנת PII: הסרת מספרי טלפון מ-breadcrumbs, headers, ו-request data
"""
import re

import sentry_sdk

from app.core.logging import get_logger, get_correlation_id

logger = get_logger(__name__)

# ביטוי רגולרי לזיהוי מספרי טלפון ישראליים/בינלאומיים
_PHONE_RE = re.compile(
    r"(\+?972|0)[\s-]?([2-9]\d)[\s-]?(\d{3})[\s-]?(\d{4})"
)
_PHONE_PLACEHOLDER = "[REDACTED_PHONE]"


def _scrub_phones(value: str) -> str:
    """החלפת מספרי טלפון בטקסט ב-placeholder"""
    return _PHONE_RE.sub(_PHONE_PLACEHOLDER, value)


def _scrub_dict(data: dict) -> dict:
    """סריקת מילון רקורסיבית והחלפת מספרי טלפון"""
    scrubbed = {}
    for key, val in data.items():
        if isinstance(val, str):
            scrubbed[key] = _scrub_phones(val)
        elif isinstance(val, dict):
            scrubbed[key] = _scrub_dict(val)
        elif isinstance(val, list):
            scrubbed[key] = [
                _scrub_phones(item) if isinstance(item, str)
                else _scrub_dict(item) if isinstance(item, dict)
                else item
                for item in val
            ]
        else:
            scrubbed[key] = val
    return scrubbed


def _before_send(event: dict, hint: dict) -> dict:
    """סינון PII והעשרת event לפני שליחה ל-Sentry"""
    # העשרה עם correlation ID
    correlation_id = get_correlation_id()
    if correlation_id:
        event.setdefault("tags", {})["correlation_id"] = correlation_id

    # סינון מספרי טלפון מ-exception values
    if "exception" in event:
        for exception_entry in event["exception"].get("values", []):
            if "value" in exception_entry and isinstance(exception_entry["value"], str):
                exception_entry["value"] = _scrub_phones(exception_entry["value"])

    # סינון מספרי טלפון מ-breadcrumbs
    for breadcrumb in event.get("breadcrumbs", {}).get("values", []):
        if "message" in breadcrumb and isinstance(breadcrumb["message"], str):
            breadcrumb["message"] = _scrub_phones(breadcrumb["message"])
        if "data" in breadcrumb and isinstance(breadcrumb["data"], dict):
            breadcrumb["data"] = _scrub_dict(breadcrumb["data"])

    # סינון מספרי טלפון מ-request data
    request_data = event.get("request", {})
    if "data" in request_data and isinstance(request_data["data"], dict):
        request_data["data"] = _scrub_dict(request_data["data"])
    if "headers" in request_data and isinstance(request_data["headers"], dict):
        request_data["headers"] = _scrub_dict(request_data["headers"])

    # סינון מ-message
    if "message" in event and isinstance(event["message"], str):
        event["message"] = _scrub_phones(event["message"])

    return event


def _before_send_transaction(event: dict, hint: dict) -> dict:
    """סינון PII מ-transactions (ביצועים)"""
    # סינון מספרי טלפון מ-transaction name
    if "transaction" in event and isinstance(event["transaction"], str):
        event["transaction"] = _scrub_phones(event["transaction"])

    return event


def init_sentry() -> None:
    """אתחול Sentry SDK — קריאה חד-פעמית בעלייה של FastAPI או Celery worker.

    אם SENTRY_DSN ריק, Sentry לא יאותחל ואין תופעות לוואי.
    """
    from app.core.config import settings

    if not settings.SENTRY_DSN:
        logger.info("SENTRY_DSN לא מוגדר — Sentry מושבת")
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        before_send=_before_send,
        before_send_transaction=_before_send_transaction,
        # הסרת ערכים רגישים מ-request headers/cookies
        send_default_pii=False,
        # מידע על הגרסה לזיהוי regressions
        release=f"shipment-bot@1.0.0",
        # מניעת שליחת health check transactions
        traces_sampler=_traces_sampler,
    )

    logger.info(
        "Sentry אותחל בהצלחה",
        extra_data={
            "environment": settings.SENTRY_ENVIRONMENT,
            "traces_sample_rate": settings.SENTRY_TRACES_SAMPLE_RATE,
        },
    )


def _traces_sampler(sampling_context: dict) -> float:
    """דגימה חכמה — מסנן health checks ונותן עדיפות לשגיאות"""
    from app.core.config import settings

    transaction_context = sampling_context.get("transaction_context", {})
    name = transaction_context.get("name", "")

    # health checks לא מעניינים — לא לדגום
    if name and ("/health" in name):
        return 0.0

    return settings.SENTRY_TRACES_SAMPLE_RATE


def set_sentry_user(user_id: int, role: str | None = None) -> None:
    """הגדרת פרטי המשתמש הנוכחי ב-Sentry scope — לשיוך שגיאות למשתמש"""
    sentry_sdk.set_user({"id": str(user_id), "role": role or "unknown"})


def capture_message(message: str, level: str = "info") -> None:
    """שליחת הודעה ידנית ל-Sentry (לאירועים חשובים שאינם exceptions)"""
    sentry_sdk.capture_message(message, level=level)
