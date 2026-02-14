"""
בדיקות ל-app/db/queries.py — פונקציות helper ל-eager loading.

מכסה:
- delivery_with_relations() טוען sender, courier, requesting_courier בשאילתה אחת
- delivery_with_sender() טוען sender בלבד
- אינדקס חדש על approved_by_id קיים במודל
- queue_capture_notification חוסך query כשמועבר preloaded_sender
- שליפת רשימת משלוחים עם relationships ללא queries מיותרות
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.user import UserRole
from app.db.queries import delivery_with_relations, delivery_with_sender
from app.domain.services.outbox_service import OutboxService


class TestDeliveryWithRelations:
    """בדיקות ל-delivery_with_relations()"""

    @pytest.mark.asyncio
    async def test_returns_joinedload_options(self) -> None:
        """הפונקציה מחזירה רשימה של options לשימוש ב-select"""
        options = delivery_with_relations()
        assert isinstance(options, list)
        assert len(options) == 3  # sender, courier, requesting_courier

    @pytest.mark.asyncio
    async def test_eager_loads_sender_and_courier(
        self,
        db_session: AsyncSession,
        user_factory,
        delivery_factory,
        async_engine,
    ) -> None:
        """שליפת משלוח עם delivery_with_relations טוענת sender ו-courier ב-JOIN אחד"""
        from tests.test_performance import QueryCounter

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
        )
        delivery = await delivery_factory(
            sender_id=sender.id,
            courier_id=courier.id,
            status=DeliveryStatus.CAPTURED,
        )

        # שאילתה אחת עם eager loading
        async with QueryCounter(async_engine) as counter:
            result = await db_session.execute(
                select(Delivery)
                .options(*delivery_with_relations())
                .where(Delivery.id == delivery.id)
            )
            loaded = result.scalars().unique().one()
            # גישה ל-relationships — אסור לייצר queries נוספות
            _ = loaded.sender.name
            _ = loaded.courier.name

        # שאילתה אחת בלבד (SELECT עם JOINs)
        assert counter.count == 1, (
            f"צפינו ל-1 שאילתא, קיבלנו {counter.count}: {counter.queries}"
        )

    @pytest.mark.asyncio
    async def test_eager_loads_requesting_courier(
        self,
        db_session: AsyncSession,
        user_factory,
        delivery_factory,
        async_engine,
    ) -> None:
        """delivery_with_relations טוען גם requesting_courier"""
        from tests.test_performance import QueryCounter

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח מבקש",
            role=UserRole.COURIER,
        )

        delivery = Delivery(
            sender_id=sender.id,
            pickup_address="רחוב הרצל 1, תל אביב",
            dropoff_address="רחוב בן יהודה 50, ירושלים",
            status=DeliveryStatus.PENDING_APPROVAL,
            requesting_courier_id=courier.id,
        )
        db_session.add(delivery)
        await db_session.commit()
        await db_session.refresh(delivery)

        async with QueryCounter(async_engine) as counter:
            result = await db_session.execute(
                select(Delivery)
                .options(*delivery_with_relations())
                .where(Delivery.id == delivery.id)
            )
            loaded = result.scalars().unique().one()
            _ = loaded.requesting_courier.name

        assert counter.count == 1, (
            f"צפינו ל-1 שאילתא, קיבלנו {counter.count}: {counter.queries}"
        )


class TestDeliveryWithSender:
    """בדיקות ל-delivery_with_sender()"""

    @pytest.mark.asyncio
    async def test_returns_single_option(self) -> None:
        """הפונקציה מחזירה option אחד (sender בלבד)"""
        options = delivery_with_sender()
        assert isinstance(options, list)
        assert len(options) == 1

    @pytest.mark.asyncio
    async def test_eager_loads_sender_only(
        self,
        db_session: AsyncSession,
        user_factory,
        delivery_factory,
        async_engine,
    ) -> None:
        """שליפה עם delivery_with_sender טוענת sender בלי query נוסף"""
        from tests.test_performance import QueryCounter

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )
        delivery = await delivery_factory(sender_id=sender.id)

        async with QueryCounter(async_engine) as counter:
            result = await db_session.execute(
                select(Delivery)
                .options(*delivery_with_sender())
                .where(Delivery.id == delivery.id)
            )
            loaded = result.scalars().unique().one()
            _ = loaded.sender.name

        assert counter.count == 1, (
            f"צפינו ל-1 שאילתא, קיבלנו {counter.count}: {counter.queries}"
        )


class TestApprovedByIndex:
    """בדיקה שה-index על approved_by_id מוגדר במודל"""

    def test_approved_by_id_has_index(self) -> None:
        """העמודה approved_by_id חייבת להיות מאונדקסת"""
        col = Delivery.__table__.c.approved_by_id
        assert col.index is True, "approved_by_id חסר index=True"


class TestCaptureNotificationSenderPassthrough:
    """בדיקה ש-queue_capture_notification לא שולף sender כשמועבר מראש"""

    @pytest.mark.asyncio
    async def test_no_extra_query_when_sender_provided(
        self,
        db_session: AsyncSession,
        user_factory,
        delivery_factory,
        async_engine,
    ) -> None:
        """כשמעבירים sender ל-queue_capture_notification, אין query נוסף ל-users"""
        from tests.test_performance import QueryCounter

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="tg_sender_1",
        )
        delivery = await delivery_factory(sender_id=sender.id)

        svc = OutboxService(db_session)

        async with QueryCounter(async_engine) as counter:
            messages = await svc.queue_capture_notification(
                delivery, courier_id=999, preloaded_sender=sender
            )

        # שאילתה אחת בלבד — INSERT של הודעת outbox (ללא SELECT על users)
        select_count = sum(
            1 for q in counter.queries if q.strip().upper().startswith("SELECT")
        )
        assert select_count == 0, (
            f"צפינו ל-0 SELECT queries, קיבלנו {select_count}: {counter.queries}"
        )
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_fallback_query_when_sender_not_provided(
        self,
        db_session: AsyncSession,
        user_factory,
        delivery_factory,
        async_engine,
    ) -> None:
        """כשלא מעבירים sender, הפונקציה שולפת אותו מה-DB (fallback)"""
        from tests.test_performance import QueryCounter

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="tg_sender_2",
        )
        delivery = await delivery_factory(sender_id=sender.id)

        svc = OutboxService(db_session)

        async with QueryCounter(async_engine) as counter:
            messages = await svc.queue_capture_notification(
                delivery, courier_id=999
            )

        # שאילתה אחת SELECT (fallback) + INSERT outbox
        select_count = sum(
            1 for q in counter.queries if q.strip().upper().startswith("SELECT")
        )
        assert select_count == 1, (
            f"צפינו ל-1 SELECT fallback, קיבלנו {select_count}: {counter.queries}"
        )
        assert len(messages) == 1


class TestDeliveryListEagerLoading:
    """בדיקה שרשימת משלוחים עם relationships לא מייצרת queries מיותרות"""

    @pytest.mark.asyncio
    async def test_list_deliveries_with_relations_single_query(
        self,
        db_session: AsyncSession,
        user_factory,
        delivery_factory,
        async_engine,
    ) -> None:
        """שליפת מספר משלוחים + גישה ל-sender/courier — שאילתה אחת בלבד"""
        from tests.test_performance import QueryCounter

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
        )
        # יצירת 5 משלוחים
        for _ in range(5):
            await delivery_factory(
                sender_id=sender.id,
                courier_id=courier.id,
                status=DeliveryStatus.CAPTURED,
            )

        async with QueryCounter(async_engine) as counter:
            result = await db_session.execute(
                select(Delivery)
                .options(*delivery_with_relations())
                .where(Delivery.status == DeliveryStatus.CAPTURED)
            )
            deliveries = list(result.scalars().unique().all())
            # גישה ל-relationships על כל המשלוחים — אסור queries נוספות
            for d in deliveries:
                _ = d.sender.name
                _ = d.courier.name

        assert len(deliveries) == 5
        # שאילתה אחת בלבד (SELECT עם JOINs) — לא N+1
        assert counter.count == 1, (
            f"צפינו ל-1 שאילתא, קיבלנו {counter.count}: {counter.queries}"
        )
