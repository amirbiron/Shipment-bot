"""
Delivery Service - Handles delivery creation and management
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.delivery import Delivery, DeliveryStatus
from app.domain.services.outbox_service import OutboxService


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
            from app.db.models.station import Station
            station_result = await self.db.execute(
                select(Station).where(Station.id == station_id)
            )
            station = station_result.scalar_one_or_none()

        # שידור דרך outbox (לקבוצת תחנה או ברודקאסט פרטני)
        await self.outbox_service.queue_delivery_broadcast(delivery, station)

        await self.db.commit()
        await self.db.refresh(delivery)

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
        """Mark a delivery as delivered"""
        delivery = await self.get_delivery(delivery_id)
        if delivery and delivery.status == DeliveryStatus.CAPTURED:
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = datetime.utcnow()
            await self.db.commit()
            await self.db.refresh(delivery)
        return delivery

    async def cancel_delivery(self, delivery_id: int) -> Optional[Delivery]:
        """Cancel a delivery"""
        delivery = await self.get_delivery(delivery_id)
        if delivery and delivery.status == DeliveryStatus.OPEN:
            delivery.status = DeliveryStatus.CANCELLED
            await self.db.commit()
            await self.db.refresh(delivery)
        return delivery
