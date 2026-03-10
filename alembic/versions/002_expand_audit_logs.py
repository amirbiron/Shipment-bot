"""הרחבת טבלת audit_logs — שדות entity, old/new values, station_id nullable

Revision ID: 002_expand_audit
Revises: 001_initial
Create Date: 2026-03-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


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
    op.add_column("audit_logs", sa.Column("old_value", sa.JSON(), nullable=True))
    op.add_column("audit_logs", sa.Column("new_value", sa.JSON(), nullable=True))

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

    op.drop_column("audit_logs", "new_value")
    op.drop_column("audit_logs", "old_value")
    op.drop_column("audit_logs", "entity_id")
    op.drop_column("audit_logs", "entity_type")

    op.alter_column("audit_logs", "station_id", nullable=False)
