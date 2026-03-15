"""
Audit Event Listeners — רשת ביטחון לזיהוי שינויים רגישים ללא audit

Watchdog שמתריע ב-warning log כשמזהה שינויים במודלים רגישים
(Delivery.status, User.role, User.approval_status, CourierWallet.balance)
שלא לוו ברשומת AuditLog באותו flush.

אינו חוסם פעולות — רק מתריע על פערים פוטנציאליים.
"""
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.core.logging import get_logger

logger = get_logger(__name__)

# שדות רגישים למעקב — {שם_מודל: {שם_שדה: תיאור}}
_WATCHED_FIELDS: dict[str, dict[str, str]] = {
    "Delivery": {
        "status": "סטטוס משלוח",
    },
    "User": {
        "role": "תפקיד משתמש",
        "approval_status": "סטטוס אישור",
    },
    "CourierWallet": {
        "balance": "יתרת ארנק",
    },
}


def _on_after_flush(session: Session, flush_context: object) -> None:
    """בודק אחרי flush אם יש שינויים רגישים ללא audit מלווה.

    הבדיקה: אם יש dirty object עם שדה רגיש שהשתנה,
    ואין AuditLog חדש באותו flush — מדפיס warning.
    """
    # בדיקה אם נוצרו רשומות AuditLog באותו flush
    from app.db.models.audit_log import AuditLog
    has_audit = any(
        isinstance(obj, AuditLog) for obj in session.new
    )

    if has_audit:
        # יש audit — סביר שהשינויים מכוסים
        return

    # בדיקת אובייקטים שהשתנו (dirty)
    for obj in list(session.dirty):
        model_name = type(obj).__name__
        watched = _WATCHED_FIELDS.get(model_name)
        if not watched:
            continue

        insp = inspect(obj)
        for field_name, field_desc in watched.items():
            history = insp.attrs[field_name].history
            if not history.has_changes():
                continue

            old_val = history.deleted[0] if history.deleted else None
            new_val = history.added[0] if history.added else None

            # המרת enums לערכי מחרוזת לקריאות בלוג
            if hasattr(old_val, "value"):
                old_val = old_val.value
            if hasattr(new_val, "value"):
                new_val = new_val.value

            logger.warning(
                "שינוי רגיש ללא רשומת audit מלווה",
                extra_data={
                    "model": model_name,
                    "field": field_name,
                    "field_desc": field_desc,
                    "old_value": str(old_val),
                    "new_value": str(new_val),
                    "object_id": getattr(obj, "id", None),
                },
            )


def register_audit_listeners(sync_engine: object) -> None:
    """רישום event listeners על sync engine.

    נקרא מ-main.py עם engine.sync_engine (כי SQLAlchemy async
    מריץ events דרך ה-sync engine הפנימי).
    """
    event.listen(Session, "after_flush", _on_after_flush)
    logger.info("Audit watchdog event listeners רשומים")
