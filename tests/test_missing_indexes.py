"""
בדיקות לוידוא שאינדקסים קריטיים מוגדרים על עמודות FK ועמודות סינון תכופות.

ראה: https://github.com/amirbiron/Shipment-bot/issues/180
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.db.models.delivery import Delivery
from app.db.models.station import Station
from app.db.models.outbox_message import OutboxMessage
from app.db.models.user import User


def _column_has_index(model_class: type, column_name: str) -> bool:
    """בודק אם לעמודה יש index=True במודל SQLAlchemy"""
    mapper = inspect(model_class)
    col = mapper.columns[column_name]
    return col.index is True


# ============================================================================
# טבלת deliveries — אינדקסים על FKs קריטיים
# ============================================================================

_DELIVERY_INDEXED_COLUMNS = [
    ("sender_id", "שליפת משלוחים לפי שולח"),
    ("courier_id", "שליפת משלוחים לפי שליח"),
    ("requesting_courier_id", "זרימת אישור משלוח"),
    ("station_id", "שליפת משלוחים לפי תחנה"),
    ("status", "סינון לפי סטטוס"),
]


@pytest.mark.unit
@pytest.mark.parametrize("column_name, description", _DELIVERY_INDEXED_COLUMNS)
def test_delivery_column_has_index(column_name: str, description: str) -> None:
    """עמודות FK קריטיות בטבלת deliveries חייבות אינדקס"""
    assert _column_has_index(Delivery, column_name), (
        f"Delivery.{column_name} ({description}) חסר אינדקס — "
        f"יגרום ל-full table scan בשאילתות תכופות"
    )


# ============================================================================
# טבלת stations — אינדקס על FK
# הערה: is_active מכוסה באינדקס חלקי ב-SQL (idx_stations_active_owner)
#        ולא ב-index=True במודל, כי אינדקס רגיל על בוליאני אינו אפקטיבי
# ============================================================================

@pytest.mark.unit
def test_station_owner_id_has_index() -> None:
    """עמודת owner_id בטבלת stations חייבת אינדקס"""
    assert _column_has_index(Station, "owner_id"), (
        "Station.owner_id (שליפת תחנות לפי בעלים) חסר אינדקס — "
        "יגרום ל-full table scan בשאילתות תכופות"
    )


@pytest.mark.unit
def test_station_is_active_no_regular_index() -> None:
    """is_active לא צריך index=True רגיל — יש אינדקס חלקי ב-SQL"""
    mapper = inspect(Station)
    col = mapper.columns["is_active"]
    assert col.index is not True, (
        "Station.is_active לא צריך index=True — "
        "אינדקס רגיל על בוליאני אינו אפקטיבי. "
        "יש אינדקס חלקי idx_stations_active_owner ב-SQL."
    )


# ============================================================================
# טבלת outbox_messages — אינדקסים לסינון לפי נמען וסטטוס
# הערה: next_retry_at מכוסה באינדקס חלקי ב-schema.sql (idx_outbox_next_retry)
#        ולא ב-index=True במודל
# ============================================================================

_OUTBOX_INDEXED_COLUMNS = [
    ("recipient_id", "סינון הודעות לפי נמען"),
    ("status", "סינון לפי סטטוס הודעה"),
]


@pytest.mark.unit
@pytest.mark.parametrize("column_name, description", _OUTBOX_INDEXED_COLUMNS)
def test_outbox_column_has_index(column_name: str, description: str) -> None:
    """עמודות סינון בטבלת outbox_messages חייבות אינדקס"""
    assert _column_has_index(OutboxMessage, column_name), (
        f"OutboxMessage.{column_name} ({description}) חסר אינדקס — "
        f"יגרום ל-full table scan בשאילתות תכופות"
    )


@pytest.mark.unit
def test_outbox_next_retry_at_no_regular_index() -> None:
    """next_retry_at לא צריך index=True רגיל — יש אינדקס חלקי ב-schema.sql"""
    mapper = inspect(OutboxMessage)
    col = mapper.columns["next_retry_at"]
    assert col.index is not True, (
        "OutboxMessage.next_retry_at לא צריך index=True — "
        "יש אינדקס חלקי idx_outbox_next_retry ב-schema.sql "
        "שמכסה רק הודעות בסטטוס pending/failed."
    )


# ============================================================================
# טבלת users — אינדקס על עמודת תפקיד
# ============================================================================

_USER_INDEXED_COLUMNS = [
    ("role", "סינון לפי תפקיד"),
    ("phone_number", "חיפוש לפי טלפון"),
    ("approval_status", "סינון לפי סטטוס אישור"),
]


@pytest.mark.unit
@pytest.mark.parametrize("column_name, description", _USER_INDEXED_COLUMNS)
def test_user_column_has_index(column_name: str, description: str) -> None:
    """עמודות סינון בטבלת users חייבות אינדקס"""
    mapper = inspect(User)
    col = mapper.columns[column_name]
    # phone_number הוא unique (שמשמיע index), השאר צריכים index=True
    has_index = col.index is True or col.unique is True
    assert has_index, (
        f"User.{column_name} ({description}) חסר אינדקס — "
        f"יגרום ל-full table scan בשאילתות תכופות"
    )


# ============================================================================
# בדיקת קובץ מיגרציה — ולידציה שהמיגרציה קיימת ומכילה רק אינדקסים חדשים
# ============================================================================

@pytest.mark.unit
def test_migration_file_exists() -> None:
    """קובץ מיגרציה 005 לאינדקסים חסרים חייב להתקיים"""
    from pathlib import Path

    migration_path = Path(__file__).parent.parent / "migrations" / "005_add_missing_indexes.sql"
    assert migration_path.exists(), (
        f"קובץ מיגרציה חסר: {migration_path} — "
        f"נדרש ליצירת האינדקסים ב-PostgreSQL"
    )


@pytest.mark.unit
def test_migration_contains_only_new_indexes() -> None:
    """קובץ מיגרציה 005 חייב לכלול רק אינדקסים שבאמת חסרים (לא כפולים)"""
    from pathlib import Path

    migration_path = Path(__file__).parent.parent / "migrations" / "005_add_missing_indexes.sql"
    content = migration_path.read_text()

    # אינדקסים שחייבים להיות במיגרציה (באמת חסרים)
    expected_indexes = [
        "idx_stations_owner_id",
        "idx_stations_active_owner",
        "idx_outbox_recipient_id",
    ]

    for index_name in expected_indexes:
        assert index_name in content, (
            f"אינדקס {index_name} חסר בקובץ המיגרציה"
        )

    # אינדקסים שלא צריכים להיות במיגרציה (כבר קיימים במיגרציות/schema קודמים)
    # בודקים רק שורות CREATE INDEX (לא הערות)
    duplicate_indexes = [
        "idx_deliveries_sender_id",
        "idx_deliveries_courier_id",
        "idx_deliveries_requesting_courier_id",
        "idx_users_role",
    ]

    create_lines = [
        line for line in content.splitlines()
        if line.strip().startswith("CREATE INDEX")
    ]
    create_text = "\n".join(create_lines)

    for index_name in duplicate_indexes:
        assert index_name not in create_text, (
            f"אינדקס {index_name} כפול — כבר קיים ב-schema.sql או במיגרציה קודמת"
        )


@pytest.mark.unit
def test_migration_has_rollback_instructions() -> None:
    """קובץ מיגרציה 005 חייב לכלול הנחיות rollback"""
    from pathlib import Path

    migration_path = Path(__file__).parent.parent / "migrations" / "005_add_missing_indexes.sql"
    content = migration_path.read_text()

    assert "Rollback" in content or "rollback" in content, (
        "קובץ מיגרציה חסר הנחיות rollback"
    )
    assert "DROP INDEX" in content, (
        "קובץ מיגרציה חסר פקודות DROP INDEX ל-rollback"
    )


# ============================================================================
# בדיקת שאילתת get_pending_messages — סינון next_retry_at ברמת SQL
# ============================================================================

@pytest.mark.unit
async def test_get_pending_messages_filters_retry_in_sql(
    db_session, user_factory
) -> None:
    """get_pending_messages מסנן הודעות עם next_retry_at עתידי ברמת SQL"""
    from datetime import datetime, timedelta
    from app.domain.services.outbox_service import OutboxService
    from app.db.models.outbox_message import OutboxMessage, MessagePlatform, MessageStatus

    outbox_service = OutboxService(db_session)

    # הודעה ללא next_retry_at — צריכה להישלף
    msg_ready = OutboxMessage(
        platform=MessagePlatform.TELEGRAM,
        recipient_id="123",
        message_type="test",
        message_content={"text": "ready"},
        status=MessageStatus.PENDING,
        next_retry_at=None,
    )
    # הודעה עם next_retry_at בעבר — צריכה להישלף
    msg_past = OutboxMessage(
        platform=MessagePlatform.TELEGRAM,
        recipient_id="456",
        message_type="test",
        message_content={"text": "past"},
        status=MessageStatus.PENDING,
        next_retry_at=datetime.utcnow() - timedelta(minutes=5),
    )
    # הודעה עם next_retry_at בעתיד — לא צריכה להישלף
    msg_future = OutboxMessage(
        platform=MessagePlatform.TELEGRAM,
        recipient_id="789",
        message_type="test",
        message_content={"text": "future"},
        status=MessageStatus.PENDING,
        next_retry_at=datetime.utcnow() + timedelta(hours=1),
    )

    db_session.add_all([msg_ready, msg_past, msg_future])
    await db_session.commit()

    messages = await outbox_service.get_pending_messages(limit=100)
    message_ids = {m.id for m in messages}

    assert msg_ready.id in message_ids, "הודעה ללא next_retry_at צריכה להישלף"
    assert msg_past.id in message_ids, "הודעה עם next_retry_at בעבר צריכה להישלף"
    assert msg_future.id not in message_ids, (
        "הודעה עם next_retry_at עתידי לא צריכה להישלף — "
        "הסינון צריך לקרות ברמת SQL ולא בפייתון"
    )
