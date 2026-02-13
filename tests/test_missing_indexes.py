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
# טבלת stations — אינדקסים על FK ועמודת סינון
# ============================================================================

_STATION_INDEXED_COLUMNS = [
    ("owner_id", "שליפת תחנות לפי בעלים"),
    ("is_active", "סינון תחנות פעילות"),
]


@pytest.mark.unit
@pytest.mark.parametrize("column_name, description", _STATION_INDEXED_COLUMNS)
def test_station_column_has_index(column_name: str, description: str) -> None:
    """עמודות FK ועמודות סינון בטבלת stations חייבות אינדקס"""
    assert _column_has_index(Station, column_name), (
        f"Station.{column_name} ({description}) חסר אינדקס — "
        f"יגרום ל-full table scan בשאילתות תכופות"
    )


# ============================================================================
# טבלת outbox_messages — אינדקסים ל-polling ולסינון לפי נמען
# ============================================================================

_OUTBOX_INDEXED_COLUMNS = [
    ("next_retry_at", "polling לניסיונות חוזרים"),
    ("recipient_id", "סינון הודעות לפי נמען"),
    ("status", "סינון לפי סטטוס הודעה"),
]


@pytest.mark.unit
@pytest.mark.parametrize("column_name, description", _OUTBOX_INDEXED_COLUMNS)
def test_outbox_column_has_index(column_name: str, description: str) -> None:
    """עמודות polling וסינון בטבלת outbox_messages חייבות אינדקס"""
    assert _column_has_index(OutboxMessage, column_name), (
        f"OutboxMessage.{column_name} ({description}) חסר אינדקס — "
        f"יגרום ל-full table scan בשאילתות תכופות"
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
# בדיקת קובץ מיגרציה — ולידציה שהמיגרציה קיימת
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
def test_migration_contains_all_indexes() -> None:
    """קובץ מיגרציה 005 חייב לכלול את כל האינדקסים הנדרשים"""
    from pathlib import Path

    migration_path = Path(__file__).parent.parent / "migrations" / "005_add_missing_indexes.sql"
    content = migration_path.read_text()

    expected_indexes = [
        "idx_deliveries_sender_id",
        "idx_deliveries_courier_id",
        "idx_deliveries_requesting_courier_id",
        "idx_stations_owner_id",
        "idx_stations_is_active",
        "idx_outbox_next_retry",
        "idx_outbox_recipient_id",
        "idx_users_role",
    ]

    for index_name in expected_indexes:
        assert index_name in content, (
            f"אינדקס {index_name} חסר בקובץ המיגרציה"
        )
