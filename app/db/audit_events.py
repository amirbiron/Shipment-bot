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

    לכל אובייקט שהשתנה, בודק האם יש רשומת AuditLog חדשה באותו flush
    שמכסה את entity_id שלו. אם לא — מתריע ב-warning log.
    """
    from app.db.models.audit_log import AuditLog

    # איסוף entity_ids שמכוסים ב-audit — בודקים per-object
    audited_entities: set[tuple[str, int | None]] = set()
    for obj in session.new:
        if isinstance(obj, AuditLog):
            audited_entities.add((obj.entity_type or "", obj.entity_id))

    # בדיקת אובייקטים שהשתנו (dirty)
    for obj in list(session.dirty):
        model_name = type(obj).__name__
        watched = _WATCHED_FIELDS.get(model_name)
        if not watched:
            continue

        obj_id = getattr(obj, "id", None)

        # מיפוי שם מודל ל-entity_type שמשמש ב-AuditLog
        entity_type_map = {
            "Delivery": "delivery",
            "User": "user",
            "CourierWallet": "wallet",
        }
        entity_type = entity_type_map.get(model_name, model_name.lower())

        # עבור CourierWallet, ה-entity_id הוא courier_id
        entity_id = obj_id
        if model_name == "CourierWallet":
            entity_id = getattr(obj, "courier_id", obj_id)

        # בדיקה אם האובייקט הספציפי הזה מכוסה ב-audit
        is_covered = (entity_type, entity_id) in audited_entities

        if is_covered:
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
                    "object_id": obj_id,
                },
            )


def register_audit_listeners() -> None:
    """רישום event listeners ברמת Session (גלובלי).

    SQLAlchemy async מריץ events דרך ה-sync session הפנימי,
    לכן ה-listener נרשם על Session class ולא על engine ספציפי.
    """
    event.listen(Session, "after_flush", _on_after_flush)
    logger.info("Audit watchdog event listeners רשומים")
