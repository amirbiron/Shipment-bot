"""הרחבת טבלת audit_logs — שדות entity, old/new values, station_id nullable

Revision ID: 002_expand_audit
Revises: 001_initial
Create Date: 2026-03-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "002_expand_audit"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """הרחבת טבלת audit_logs למערכת audit מקיפה"""
    # station_id הופך ל-nullable — פעולות כמו אישור שליח לא קשורות לתחנה
    op.alter_column("audit_logs", "station_id", nullable=True)

    # שדות חדשים
    op.add_column("audit_logs", sa.Column("entity_type", sa.String(50), nullable=True))
    op.add_column("audit_logs", sa.Column("entity_id", sa.BigInteger(), nullable=True))
    op.add_column("audit_logs", sa.Column("old_value", JSONB(), nullable=True))
    op.add_column("audit_logs", sa.Column("new_value", JSONB(), nullable=True))

    # המרת details מ-JSON ל-JSONB לעקביות עם שאר העמודות
    op.alter_column(
        "audit_logs",
        "details",
        type_=JSONB(),
        postgresql_using="details::jsonb",
    )

    # אינדקסים
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])
    op.create_index(
        "ix_audit_logs_entity",
        "audit_logs",
        ["entity_type", "entity_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """שחזור — הסרת שדות חדשים והחזרת station_id ל-NOT NULL"""
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_type", table_name="audit_logs")

    # החזרת details ל-JSON
    op.alter_column(
        "audit_logs",
        "details",
        type_=sa.JSON(),
        postgresql_using="details::json",
    )

    op.drop_column("audit_logs", "new_value")
    op.drop_column("audit_logs", "old_value")
    op.drop_column("audit_logs", "entity_id")
    op.drop_column("audit_logs", "entity_type")

    # מחיקת רשומות ללא station_id לפני החזרת NOT NULL — רשומות אלה נוצרו
    # רק אחרי ה-upgrade (אישור שליח, ארנק) ואין להן משמעות בסכמה הישנה
    op.execute("DELETE FROM audit_logs WHERE station_id IS NULL")
    op.alter_column("audit_logs", "station_id", nullable=False)
