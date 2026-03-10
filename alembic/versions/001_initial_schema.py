"""מיגרציה ראשונית — stamp של הסכמה הקיימת

הסכמה כבר קיימת ב-DB דרך create_all + מיגרציות ידניות (001-014).
מיגרציה זו מסמנת את נקודת ההתחלה של Alembic בלבד.

Revision ID: 001_initial
Revises:
Create Date: 2026-03-10
"""
from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """הסכמה כבר קיימת — stamp בלבד.

    כל הטבלאות נוצרו דרך Base.metadata.create_all ומיגרציות ידניות.
    Alembic מתחיל לעקוב משלב זה ואילך.
    """
    pass


def downgrade() -> None:
    """אין downgrade למיגרציה ראשונית — הסכמה נוצרה לפני Alembic."""
    pass
