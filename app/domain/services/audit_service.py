"""
Audit Service — שירות מרכזי לרישום פעולות רגישות בלוג ביקורת

מספק ממשק אחיד לרישום כל סוגי הפעולות הרגישות:
שינויי הרשאות, פעולות ארנק, שינויי סטטוס משלוח, ופעולות מנהלתיות.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_log import AuditLog, AuditActionType
from app.core.logging import get_logger

logger = get_logger(__name__)


class AuditService:
    """שירות מרכזי לרישום פעולות רגישות בלוג ביקורת"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        actor_user_id: int,
        action: AuditActionType,
        station_id: int | None = None,
        target_user_id: int | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        details: dict | None = None,
    ) -> None:
        """רישום פעולה רגישה בלוג ביקורת — באותה טרנזקציה עם הפעולה.

        חובה לקרוא לפני commit() כדי להבטיח אטומיות.
        """
        entry = AuditLog(
            station_id=station_id,
            actor_user_id=actor_user_id,
            action=action,
            target_user_id=target_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=old_value,
            new_value=new_value,
            details=details,
        )
        self.db.add(entry)
        logger.info(
            "רשומת audit נוצרה",
            extra_data={
                "action": action.value,
                "actor_user_id": actor_user_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
            },
        )

    async def record_delivery_status_change(
        self,
        actor_user_id: int,
        delivery_id: int,
        old_status: str,
        new_status: str,
        station_id: int | None = None,
        details: dict | None = None,
    ) -> None:
        """רישום שינוי סטטוס משלוח"""
        await self.record(
            actor_user_id=actor_user_id,
            action=AuditActionType.DELIVERY_STATUS_CHANGED,
            station_id=station_id,
            entity_type="delivery",
            entity_id=delivery_id,
            old_value={"status": old_status},
            new_value={"status": new_status},
            details=details,
        )

    async def record_courier_approval(
        self,
        actor_user_id: int,
        target_user_id: int,
        action: AuditActionType,
        old_status: str | None = None,
        new_status: str | None = None,
        details: dict | None = None,
    ) -> None:
        """רישום שינוי סטטוס אישור שליח"""
        await self.record(
            actor_user_id=actor_user_id,
            action=action,
            target_user_id=target_user_id,
            entity_type="user",
            entity_id=int(target_user_id),
            old_value={"approval_status": old_status} if old_status else None,
            new_value={"approval_status": new_status} if new_status else None,
            details=details,
        )

    async def record_wallet_operation(
        self,
        actor_user_id: int,
        courier_id: int,
        action: AuditActionType,
        amount: str,
        balance_after: str,
        delivery_id: int | None = None,
        station_id: int | None = None,
        details: dict | None = None,
    ) -> None:
        """רישום פעולת ארנק"""
        await self.record(
            actor_user_id=actor_user_id,
            action=action,
            station_id=station_id,
            target_user_id=courier_id,
            entity_type="wallet",
            entity_id=int(courier_id),
            new_value={"amount": amount, "balance_after": balance_after},
            details={
                **(details or {}),
                "delivery_id": delivery_id,
            } if delivery_id else details,
        )

    async def get_entity_audit_trail(
        self,
        entity_type: str,
        entity_id: int,
        limit: int = 50,
    ) -> list[AuditLog]:
        """שליפת היסטוריית פעולות לישות ספציפית"""
        result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.entity_type == entity_type,
                AuditLog.entity_id == entity_id,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_audit_logs(
        self,
        station_id: int | None = None,
        action: AuditActionType | None = None,
        actor_user_id: int | None = None,
        entity_type: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuditLog], int]:
        """שליפת לוג ביקורת עם סינון ו-pagination"""
        conditions = []
        if station_id is not None:
            conditions.append(AuditLog.station_id == station_id)
        if action is not None:
            conditions.append(AuditLog.action == action)
        if actor_user_id is not None:
            conditions.append(AuditLog.actor_user_id == actor_user_id)
        if entity_type is not None:
            conditions.append(AuditLog.entity_type == entity_type)
        if date_from is not None:
            conditions.append(AuditLog.created_at >= date_from)
        if date_to is not None:
            conditions.append(AuditLog.created_at <= date_to)

        # ספירה
        count_query = select(func.count(AuditLog.id))
        if conditions:
            count_query = count_query.where(*conditions)
        total = (await self.db.execute(count_query)).scalar() or 0

        # שליפה עם pagination
        query = (
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if conditions:
            query = query.where(*conditions)
        result = await self.db.execute(query)
        entries = list(result.scalars().all())

        return entries, total
