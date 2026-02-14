"""
Delivery Query Helpers — פונקציות עזר לשאילתות נפוצות עם eager loading.

מונע N+1 queries על ידי טעינת relationships מראש (joinedload).
שימוש:
    result = await db.execute(
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .options(*delivery_with_relations())
    )

הערה חשובה: אסור לשלב joinedload עם .with_for_update() —
ב-PostgreSQL הנעילה חלה על כל הטבלאות ב-JOIN ולא רק על הטבלה הראשית.
"""
from typing import List

from sqlalchemy.orm import joinedload, Load

from app.db.models.delivery import Delivery


def delivery_with_relations() -> List[Load]:
    """options נפוצים לשליפת משלוח עם משתמשים קשורים.

    כולל: sender, courier, requesting_courier.
    מתאים לכל שאילתה שצריכה להציג פרטי משתמש ליד משלוח.
    """
    return [
        joinedload(Delivery.sender),
        joinedload(Delivery.courier),
        joinedload(Delivery.requesting_courier),
    ]
