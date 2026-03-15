"""הוספת ערכי AuditActionType חדשים — מערכת audit מקיפה

Revision ID: 003_audit_actions
Revises: 002_expand_audit
Create Date: 2026-03-14
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "003_audit_actions"
down_revision: Union[str, None] = "002_expand_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ערכי enum חדשים — SQLEnum ללא values_callable שומר member names (uppercase)
_NEW_VALUES = [
    "DELIVERY_CAPTURED",
    "DELIVERY_RELEASED",
    "DELIVERY_REQUESTED",
    "DELIVERY_APPROVED",
    "DELIVERY_REJECTED",
    "WALLET_REFUND",
    "AUTO_BLACKLIST_ADDED",
]


def upgrade() -> None:
    """הוספת ערכי AuditActionType חדשים ל-PostgreSQL enum"""
    for value in _NEW_VALUES:
        op.execute(
            f"ALTER TYPE auditactiontype ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    """PostgreSQL לא תומך בהסרת ערכים מ-enum — אין downgrade.

    הערכים החדשים נשארים ב-enum אבל לא ישמשו. אם נדרש downgrade מלא:
    1. ליצור enum חדש ללא הערכים
    2. להמיר את העמודה
    3. למחוק את ה-enum הישן
    """
    pass
