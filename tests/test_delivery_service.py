"""
בדיקות יחידה ל-DeliveryService — יצירה, ניהול וסטטוסים של משלוחים
"""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.user import UserRole
from app.domain.services.delivery_service import DeliveryService


@pytest.mark.unit
class TestCreateDelivery:
    """בדיקות יצירת משלוח"""

    async def test_create_delivery_basic(self, db_session, user_factory):
        """יצירת משלוח בסיסי"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)

        service = DeliveryService(db_session)
        with patch.object(
            service.outbox_service, "queue_delivery_broadcast", new_callable=AsyncMock
        ):
            delivery = await service.create_delivery(
                sender_id=sender.id,
                pickup_address="רחוב הרצל 1, תל אביב",
                dropoff_address="רחוב בן יהודה 50, ירושלים",
                fee=15.0,
            )

        assert delivery.id is not None
        assert delivery.sender_id == sender.id
        assert delivery.status == DeliveryStatus.OPEN
        assert delivery.pickup_address == "רחוב הרצל 1, תל אביב"
        assert delivery.fee == Decimal("15.00")
        assert delivery.token is not None

    async def test_create_delivery_with_contacts(self, db_session, user_factory):
        """יצירת משלוח עם פרטי קשר"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)

        service = DeliveryService(db_session)
        with patch.object(
            service.outbox_service, "queue_delivery_broadcast", new_callable=AsyncMock
        ):
            delivery = await service.create_delivery(
                sender_id=sender.id,
                pickup_address="כתובת איסוף",
                dropoff_address="כתובת יעד",
                pickup_contact_name="ישראל ישראלי",
                pickup_contact_phone="+972501234567",
                dropoff_contact_name="משה כהן",
            )

        assert delivery.pickup_contact_name == "ישראל ישראלי"
        assert delivery.dropoff_contact_name == "משה כהן"

    async def test_create_delivery_queues_broadcast(self, db_session, user_factory):
        """יצירת משלוח צריכה לשדר דרך outbox"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)

        service = DeliveryService(db_session)
        with patch.object(
            service.outbox_service, "queue_delivery_broadcast", new_callable=AsyncMock
        ) as mock_broadcast:
            await service.create_delivery(
                sender_id=sender.id,
                pickup_address="א",
                dropoff_address="ב",
            )

        mock_broadcast.assert_awaited_once()

    async def test_create_station_delivery_publishes_alert(self, db_session, user_factory):
        """משלוח עם station_id — צריך לפרסם התראה בזמן אמת"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)

        service = DeliveryService(db_session)
        mock_station = AsyncMock()
        mock_station.get_station = AsyncMock(return_value=None)

        with patch.object(
            service.outbox_service, "queue_delivery_broadcast", new_callable=AsyncMock
        ), patch(
            "app.domain.services.station_service.StationService",
            return_value=mock_station,
        ), patch(
            "app.domain.services.delivery_service.publish_delivery_created",
            new_callable=AsyncMock,
        ) as mock_alert:
            await service.create_delivery(
                sender_id=sender.id,
                pickup_address="א",
                dropoff_address="ב",
                station_id=1,
            )

        mock_alert.assert_awaited_once()


@pytest.mark.unit
class TestGetDeliveries:
    """בדיקות שליפת משלוחים"""

    async def test_get_delivery_by_id(self, db_session, user_factory, delivery_factory):
        """שליפת משלוח לפי מזהה"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        delivery = await delivery_factory(sender_id=sender.id)

        service = DeliveryService(db_session)
        result = await service.get_delivery(delivery.id)

        assert result is not None
        assert result.id == delivery.id

    async def test_get_delivery_nonexistent(self, db_session):
        """שליפת משלוח שלא קיים — מחזיר None"""
        service = DeliveryService(db_session)
        result = await service.get_delivery(99999)
        assert result is None

    async def test_get_open_deliveries(self, db_session, user_factory, delivery_factory):
        """שליפת כל המשלוחים הפתוחים"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        await delivery_factory(sender_id=sender.id, status=DeliveryStatus.OPEN)
        await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.OPEN,
            pickup_address="כתובת 2",
            dropoff_address="יעד 2",
        )
        await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.CAPTURED,
            pickup_address="כתובת 3",
            dropoff_address="יעד 3",
        )

        service = DeliveryService(db_session)
        open_deliveries = await service.get_open_deliveries()

        assert len(open_deliveries) == 2

    async def test_get_sender_deliveries(self, db_session, user_factory, delivery_factory):
        """שליפת משלוחים של שולח ספציפי"""
        sender1 = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        sender2 = await user_factory(phone_number="+972502222222", role=UserRole.SENDER)
        await delivery_factory(sender_id=sender1.id)
        await delivery_factory(sender_id=sender2.id)

        service = DeliveryService(db_session)
        deliveries = await service.get_sender_deliveries(sender1.id)

        assert len(deliveries) == 1
        assert deliveries[0].sender_id == sender1.id

    async def test_get_courier_deliveries(self, db_session, user_factory, delivery_factory):
        """שליפת משלוחים של שליח ספציפי"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.CAPTURED,
            courier_id=courier.id,
        )

        service = DeliveryService(db_session)
        deliveries = await service.get_courier_deliveries(courier.id)

        assert len(deliveries) == 1
        assert deliveries[0].courier_id == courier.id


@pytest.mark.unit
class TestMarkDelivered:
    """בדיקות סימון משלוח כנמסר"""

    async def test_mark_delivered_from_captured(
        self, db_session, user_factory, delivery_factory
    ):
        """סימון משלוח CAPTURED כנמסר — צריך להצליח"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.CAPTURED,
            courier_id=courier.id,
        )

        service = DeliveryService(db_session)
        result = await service.mark_delivered(delivery.id)

        assert result is not None
        assert result.status == DeliveryStatus.DELIVERED
        assert result.delivered_at is not None

    async def test_mark_delivered_from_in_progress(
        self, db_session, user_factory, delivery_factory
    ):
        """סימון משלוח IN_PROGRESS כנמסר — צריך להצליח"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.IN_PROGRESS,
            courier_id=courier.id,
        )

        service = DeliveryService(db_session)
        result = await service.mark_delivered(delivery.id)

        assert result is not None
        assert result.status == DeliveryStatus.DELIVERED

    async def test_mark_delivered_from_open_fails(
        self, db_session, user_factory, delivery_factory
    ):
        """סימון משלוח OPEN כנמסר — צריך להיכשל"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        delivery = await delivery_factory(sender_id=sender.id, status=DeliveryStatus.OPEN)

        service = DeliveryService(db_session)
        result = await service.mark_delivered(delivery.id)

        assert result is None

    async def test_mark_delivered_nonexistent(self, db_session):
        """סימון משלוח שלא קיים — מחזיר None"""
        service = DeliveryService(db_session)
        result = await service.mark_delivered(99999)
        assert result is None

    async def test_mark_delivered_station_credits_commission(
        self, db_session, user_factory, delivery_factory
    ):
        """משלוח של תחנה שנמסר — צריך לזכות עמלת תחנה"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.CAPTURED,
            courier_id=courier.id,
            fee=100.0,
        )
        delivery.station_id = 1
        await db_session.commit()

        service = DeliveryService(db_session)
        mock_station = AsyncMock()
        mock_station.credit_station_commission = AsyncMock()

        with patch(
            "app.domain.services.station_service.StationService",
            return_value=mock_station,
        ):
            result = await service.mark_delivered(delivery.id)

        assert result is not None
        assert result.status == DeliveryStatus.DELIVERED
        mock_station.credit_station_commission.assert_awaited_once_with(
            station_id=1,
            delivery_id=delivery.id,
            fee=Decimal("100.00"),
            auto_commit=False,
        )


@pytest.mark.unit
class TestCancelDelivery:
    """בדיקות ביטול משלוח"""

    async def test_cancel_open_delivery(self, db_session, user_factory, delivery_factory):
        """ביטול משלוח פתוח — צריך להצליח"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        delivery = await delivery_factory(sender_id=sender.id, status=DeliveryStatus.OPEN)

        service = DeliveryService(db_session)
        result = await service.cancel_delivery(delivery.id)

        assert result is not None
        assert result.status == DeliveryStatus.CANCELLED

    async def test_cancel_captured_delivery_ignored(
        self, db_session, user_factory, delivery_factory
    ):
        """ביטול משלוח שנתפס — לא צריך להשתנות"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.CAPTURED,
            courier_id=courier.id,
        )

        service = DeliveryService(db_session)
        result = await service.cancel_delivery(delivery.id)

        # מחזיר את המשלוח אבל לא משנה סטטוס
        assert result is not None
        assert result.status == DeliveryStatus.CAPTURED

    async def test_cancel_nonexistent_delivery(self, db_session):
        """ביטול משלוח שלא קיים — מחזיר None"""
        service = DeliveryService(db_session)
        result = await service.cancel_delivery(99999)
        assert result is None
