"""הוספת עמודת external_user_id ל-users (הכנה ל-BSUID של Meta Cloud API)

Revision ID: 005_external_user_id
Revises: 004_missing_audit_values
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005_external_user_id"
down_revision: Union[str, None] = "004_missing_audit_values"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """הוספת external_user_id (BSUID) לטבלת users, unique nullable."""
    op.add_column(
        "users",
        sa.Column("external_user_id", sa.String(length=150), nullable=True),
    )
    op.create_unique_constraint(
        "users_external_user_id_key", "users", ["external_user_id"]
    )


def downgrade() -> None:
    op.drop_constraint("users_external_user_id_key", "users", type_="unique")
    op.drop_column("users", "external_user_id")
