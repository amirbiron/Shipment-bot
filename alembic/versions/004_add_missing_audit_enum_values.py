"""הוספת ערכי AuditActionType חסרים — DELIVERY_STATUS_CHANGED, WALLET_DEBIT, WALLET_CREDIT

Revision ID: 004_missing_audit_values
Revises: 003_audit_actions
Create Date: 2026-03-21
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "004_missing_audit_values"
down_revision: Union[str, None] = "003_audit_actions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ערכים שנוספו ל-Python enum אבל חסרים ב-PostgreSQL
_MISSING_VALUES = [
    "DELIVERY_STATUS_CHANGED",
    "WALLET_DEBIT",
    "WALLET_CREDIT",
]


def upgrade() -> None:
    """הוספת ערכי enum חסרים ל-auditactiontype"""
    for value in _MISSING_VALUES:
        op.execute(
            f"ALTER TYPE auditactiontype ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    """PostgreSQL לא תומך בהסרת ערכים מ-enum — אין downgrade."""
    pass
