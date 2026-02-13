"""
תרחיש 5 — תחנה + סדרן: זרימת אישור/דחייה מלאה

מכסה:
- סדרן יוצר משלוח → שליח מבקש → סדרן מאשר → חיוב ארנק + עמלת תחנה
- דחייה → סטטוס חוזר ל-OPEN, ארנק ללא שינוי
- סדרן לא מורשה נדחה
- אישור דרך webhook (callback_query)
"""
import pytest
from sqlalchemy import select

from app.db.models.user import UserRole, ApprovalStatus
from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.outbox_message import OutboxMessage
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.shipment_workflow_service import ShipmentWorkflowService

from tests.scenarios.conftest import (
    assert_delivery_status,
    assert_outbox_count,
    assert_wallet_balance,
    assert_ledger_count,
    send_tg_callback,
)


@pytest.mark.scenario
class TestStationDispatcherFlow:
    """זרימת תחנה + סדרן — אישור ודחיית משלוחים"""

    @pytest.mark.asyncio
    async def test_station_approve_full_flow(
        self,
        db_session,
        user_factory,
        wallet_factory,
        station_factory,
        station_wallet_factory,
        dispatcher_factory,
    ):
        """זרימה מלאה: יצירה → בקשה → אישור → מסירה + עמלת תחנה"""
        # --- הכנה ---
        owner = await user_factory(
            phone_number="+972500000001",
            name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            private_group_chat_id="-100PRIVATE",
        )
        await station_wallet_factory(
            station_id=station.id, balance=0.0, commission_rate=0.10
        )

        dispatcher_user = await user_factory(
            phone_number="+972504444444",
            name="סדרן",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await dispatcher_factory(station_id=station.id, user_id=dispatcher_user.id)

        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
            telegram_chat_id="22222",
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )

        # --- שלב 1: יצירת משלוח של תחנה ---
        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=20.0,
            station_id=station.id,
        )
        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.OPEN)

        # --- שלב 2: שליח מבקש ---
        workflow = ShipmentWorkflowService(db_session)
        success, msg, _ = await workflow.request_delivery(delivery.id, courier.id)
        assert success is True, f"בקשה נכשלה: {msg}"

        pending = await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.PENDING_APPROVAL
        )
        assert pending.requesting_courier_id == courier.id

        # אימות: הודעה לסדרנים
        await assert_outbox_count(
            db_session, "delivery_request_notification", min_count=1
        )

        # --- שלב 3: סדרן מאשר ---
        success, msg, _ = await workflow.approve_delivery(
            delivery.id, dispatcher_user.id
        )
        assert success is True, f"אישור נכשל: {msg}"

        # אימות: CAPTURED + ארנק חויב + שדות אישור
        captured = await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.CAPTURED
        )
        assert captured.courier_id == courier.id
        assert captured.approved_by_id == dispatcher_user.id
        assert captured.approval_decision == "approved"

        await assert_wallet_balance(db_session, courier.id, 80.0)
        await assert_ledger_count(db_session, courier.id, 1)

        # אימות: הודעת החלטה לשליח + כרטיס סגור
        await assert_outbox_count(
            db_session, "delivery_decision_notification", min_count=1
        )
        await assert_outbox_count(db_session, "closed_shipment_card", min_count=1)

        # --- שלב 4: סימון כנמסר + עמלת תחנה ---
        result = await delivery_service.mark_delivered(delivery.id)
        assert result is not None

        await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.DELIVERED
        )

        # אימות: ארנק תחנה קיבל 10% = 2.0
        from app.db.models.station_wallet import StationWallet

        station_id_local = station.id  # שמירת ID מקומי
        sw_result = await db_session.execute(
            select(StationWallet).where(
                StationWallet.station_id == station_id_local
            ).execution_options(populate_existing=True)
        )
        station_wallet = sw_result.scalar_one()
        assert abs(station_wallet.balance - 2.0) < 0.01

    @pytest.mark.asyncio
    async def test_station_reject_returns_to_open(
        self,
        db_session,
        user_factory,
        wallet_factory,
        station_factory,
        dispatcher_factory,
    ):
        """דחייה — סטטוס חוזר ל-OPEN, ארנק ללא שינוי"""
        owner = await user_factory(
            phone_number="+972500000001",
            name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            private_group_chat_id="-100PRIVATE",
        )
        dispatcher_user = await user_factory(
            phone_number="+972504444444",
            name="סדרן",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await dispatcher_factory(station_id=station.id, user_id=dispatcher_user.id)

        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=20.0,
            station_id=station.id,
        )

        # בקשה
        workflow = ShipmentWorkflowService(db_session)
        await workflow.request_delivery(delivery.id, courier.id)

        # דחייה
        success, msg, _ = await workflow.reject_delivery(
            delivery.id, dispatcher_user.id
        )
        assert success is True, f"דחייה נכשלה: {msg}"

        # אימות: חזר ל-OPEN, ארנק לא חויב
        rejected = await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.OPEN
        )
        assert rejected.requesting_courier_id is None
        assert rejected.approval_decision == "rejected"

        await assert_wallet_balance(db_session, courier.id, 100.0)
        await assert_ledger_count(db_session, courier.id, 0)

    @pytest.mark.asyncio
    async def test_non_dispatcher_cannot_approve(
        self, db_session, user_factory, wallet_factory, station_factory
    ):
        """משתמש שאינו סדרן של התחנה — לא יכול לאשר"""
        owner = await user_factory(
            phone_number="+972500000001",
            name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)

        # סדרן לא מקושר לתחנה
        non_dispatcher = await user_factory(
            phone_number="+972504444444",
            name="לא סדרן",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=20.0,
            station_id=station.id,
        )

        # בקשה
        workflow = ShipmentWorkflowService(db_session)
        await workflow.request_delivery(delivery.id, courier.id)

        # ניסיון אישור על ידי מי שאינו סדרן — נדחה
        success, msg, _ = await workflow.approve_delivery(
            delivery.id, non_dispatcher.id
        )
        assert success is False
        assert "הרשאה" in msg

        # אימות: סטטוס לא השתנה
        await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.PENDING_APPROVAL
        )

    @pytest.mark.asyncio
    async def test_dispatcher_approve_via_telegram_callback(
        self,
        test_client,
        db_session,
        user_factory,
        wallet_factory,
        station_factory,
        station_wallet_factory,
        dispatcher_factory,
        configure_admin,
    ):
        """אישור משלוח דרך webhook — callback_query של סדרן"""
        owner = await user_factory(
            phone_number="+972500000001",
            name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            private_group_chat_id="-100PRIVATE",
        )
        await station_wallet_factory(station_id=station.id)

        dispatcher_user = await user_factory(
            phone_number="+972504444444",
            name="סדרן",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
            telegram_chat_id="44444",
        )
        await dispatcher_factory(station_id=station.id, user_id=dispatcher_user.id)

        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
            telegram_chat_id="22222",
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )

        # יצירת משלוח + בקשה
        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=20.0,
            station_id=station.id,
        )

        workflow = ShipmentWorkflowService(db_session)
        success, _, _ = await workflow.request_delivery(delivery.id, courier.id)
        assert success is True

        # שמירת IDs מקומיים לפני webhook call (שעשוי לבצע expire)
        dispatcher_chat = int(dispatcher_user.telegram_chat_id)
        delivery_id_local = delivery.id
        courier_id_local = courier.id

        # סדרן לוחץ approve דרך webhook
        data = await send_tg_callback(
            test_client,
            dispatcher_chat,
            f"approve_delivery_{delivery_id_local}",
        )

        # אימות מ-DB (לא מ-response, כי הפורמט עשוי להשתנות)
        result = await db_session.execute(
            select(Delivery).where(
                Delivery.id == delivery_id_local
            ).execution_options(populate_existing=True)
        )
        refreshed = result.scalar_one()
        assert refreshed.status == DeliveryStatus.CAPTURED
        assert refreshed.courier_id == courier_id_local
        assert refreshed.courier_id == courier.id
