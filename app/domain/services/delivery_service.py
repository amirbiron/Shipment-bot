"""
Delivery Service - Handles delivery creation and management
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models.delivery import Delivery, DeliveryStatus
from app.domain.services.outbox_service import OutboxService
from app.domain.services.alert_service import (
    publish_delivery_created,
    publish_delivery_delivered,
    publish_delivery_cancelled,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


class DeliveryService:
    """Service for managing deliveries"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.outbox_service = OutboxService(db)

    async def create_delivery(
        self,
        sender_id: int,
        pickup_address: str,
        dropoff_address: str,
        pickup_contact_name: Optional[str] = None,
        pickup_contact_phone: Optional[str] = None,
        pickup_notes: Optional[str] = None,
        dropoff_contact_name: Optional[str] = None,
        dropoff_contact_phone: Optional[str] = None,
        dropoff_notes: Optional[str] = None,
        fee: float = 10.0,
        station_id: Optional[int] = None
    ) -> Delivery:
        """
        Create a new delivery and queue broadcast messages.
        Uses transactional outbox pattern.
        """
        delivery = Delivery(
            sender_id=sender_id,
            pickup_address=pickup_address,
            dropoff_address=dropoff_address,
            pickup_contact_name=pickup_contact_name,
            pickup_contact_phone=pickup_contact_phone,
            pickup_notes=pickup_notes,
            dropoff_contact_name=dropoff_contact_name,
            dropoff_contact_phone=dropoff_contact_phone,
            dropoff_notes=dropoff_notes,
            fee=fee,
            status=DeliveryStatus.OPEN,
            station_id=station_id
        )

        self.db.add(delivery)
        await self.db.flush()  # Get delivery ID

        # שלב 4: שליפת תחנה אם קיימת — לשידור לקבוצה ציבורית
        station = None
        if station_id:
            from app.domain.services.station_service import StationService
            station_service = StationService(self.db)
            station = await station_service.get_station(station_id)

        # שידור דרך outbox (לקבוצת תחנה או ברודקאסט פרטני)
        await self.outbox_service.queue_delivery_broadcast(delivery, station)

        await self.db.commit()
        await self.db.refresh(delivery)

        # התראה בזמן אמת לפאנל — רק למשלוחי תחנה
        if station_id:
            await publish_delivery_created(
                station_id=station_id,
                delivery_id=delivery.id,
                pickup_address=pickup_address,
                dropoff_address=dropoff_address,
                fee=fee,
            )

        return delivery

    async def get_delivery(self, delivery_id: int) -> Optional[Delivery]:
        """Get delivery by ID"""
        result = await self.db.execute(
            select(Delivery).where(Delivery.id == delivery_id)
        )
        return result.scalar_one_or_none()

    async def get_open_deliveries(self) -> List[Delivery]:
        """Get all open deliveries"""
        result = await self.db.execute(
            select(Delivery)
            .where(Delivery.status == DeliveryStatus.OPEN)
            .order_by(Delivery.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_sender_deliveries(self, sender_id: int) -> List[Delivery]:
        """Get all deliveries for a sender"""
        result = await self.db.execute(
            select(Delivery)
            .where(Delivery.sender_id == sender_id)
            .order_by(Delivery.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_courier_deliveries(self, courier_id: int) -> List[Delivery]:
        """Get all deliveries assigned to a courier"""
        result = await self.db.execute(
            select(Delivery)
            .where(Delivery.courier_id == courier_id)
            .order_by(Delivery.created_at.desc())
        )
        return list(result.scalars().all())

    async def mark_delivered(self, delivery_id: int) -> Optional[Delivery]:
        """סימון משלוח כנמסר וזיכוי עמלת תחנה (10%) אם שייך לתחנה.

        מעברי סטטוס מותרים: CAPTURED → DELIVERED, IN_PROGRESS → DELIVERED.
        הזיכוי מתבצע באותה טרנזקציה עם שינוי הסטטוס לאטומיות.
        """
        # נעילת שורה למניעת זיכוי עמלה כפול בקריאות מקבילות
        result = await self.db.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .with_for_update()
        )
        delivery = result.scalar_one_or_none()
        if not delivery:
            return None

        # ולידציה של סטטוס נוכחי לפני מעבר
        valid_statuses = (DeliveryStatus.CAPTURED, DeliveryStatus.IN_PROGRESS)
        if delivery.status not in valid_statuses:
            logger.warning(
                "ניסיון לסמן משלוח כנמסר מסטטוס לא תקין",
                extra_data={
                    "delivery_id": delivery_id,
                    "current_status": delivery.status.value,
                }
            )
            return None

        try:
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = datetime.utcnow()

            # שלב 5: זיכוי עמלת תחנה אם המשלוח שייך לתחנה
            if delivery.station_id and delivery.fee and delivery.fee > 0:
                from app.domain.services.station_service import StationService
                station_service = StationService(self.db)
                await station_service.credit_station_commission(
                    station_id=delivery.station_id,
                    delivery_id=delivery.id,
                    fee=delivery.fee,
                    auto_commit=False,
                )
                logger.info(
                    "עמלת תחנה זוכתה בעת סימון משלוח כנמסר",
                    extra_data={
                        "delivery_id": delivery_id,
                        "station_id": delivery.station_id,
                        "fee": delivery.fee,
                    }
                )

            await self.db.commit()
            await self.db.refresh(delivery)

        except SQLAlchemyError as e:
            logger.error(
                "כשלון בסימון משלוח כנמסר",
                extra_data={"delivery_id": delivery_id, "error": str(e)},
                exc_info=True,
            )
            await self.db.rollback()
            return None

        # התראה בזמן אמת לפאנל — מחוץ ל-try העסקי כדי שכשלון התראה
        # לא ישפיע על תוצאת הפעולה (הפעולה כבר committed)
        try:
            if delivery.station_id:
                courier_name = ""
                if delivery.courier_id:
                    from app.db.models.user import User
                    courier_result = await self.db.execute(
                        select(User).where(User.id == delivery.courier_id)
                    )
                    courier = courier_result.scalar_one_or_none()
                    if courier:
                        courier_name = courier.full_name or courier.name or "לא צוין"
                await publish_delivery_delivered(
                    station_id=delivery.station_id,
                    delivery_id=delivery.id,
                    courier_name=courier_name,
                )
        except Exception as e:
            logger.error(
                "כשלון בפרסום התראת משלוח נמסר — הפעולה העסקית הצליחה",
                extra_data={"delivery_id": delivery_id, "error": str(e)},
                exc_info=True,
            )

        return delivery

    async def cancel_delivery(self, delivery_id: int) -> Optional[Delivery]:
        """Cancel a delivery"""
        delivery = await self.get_delivery(delivery_id)
        if delivery and delivery.status == DeliveryStatus.OPEN:
            delivery.status = DeliveryStatus.CANCELLED
            await self.db.commit()
            await self.db.refresh(delivery)

            # התראה בזמן אמת לפאנל — רק למשלוחי תחנה
            if delivery.station_id:
                await publish_delivery_cancelled(
                    station_id=delivery.station_id,
                    delivery_id=delivery.id,
                )

        return delivery
