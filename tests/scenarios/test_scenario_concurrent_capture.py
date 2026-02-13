"""
תרחיש 2 — מרוץ בין שליחים: שני שליחים מתחרים על אותו משלוח

מכסה:
- תפיסה ישירה: הראשון מצליח, השני נכשל
- בקשת אישור (תחנה): הראשון עובר ל-PENDING, השני נדחה
"""
import pytest

from app.db.models.user import UserRole, ApprovalStatus
from app.db.models.delivery import DeliveryStatus
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.capture_service import CaptureService
from app.domain.services.shipment_workflow_service import ShipmentWorkflowService

from tests.scenarios.conftest import (
    assert_delivery_status,
    assert_wallet_balance,
)


@pytest.mark.scenario
class TestConcurrentCapture:
    """מרוץ בין שליחים — רק הראשון מצליח"""

    @pytest.mark.asyncio
    async def test_first_courier_captures_second_fails(
        self, db_session, user_factory, wallet_factory
    ):
        """משלוח ישיר: שליח A תופס, שליח B נכשל"""
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="11111",
        )
        courier_a = await user_factory(
            phone_number="+972502222222",
            name="שליח א",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier_a.id, balance=100.0)

        courier_b = await user_factory(
            phone_number="+972503333333",
            name="שליח ב",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier_b.id, balance=100.0)

        # יצירת משלוח ישיר
        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
        )

        # שליח A תופס — הצלחה
        capture_service = CaptureService(db_session)
        success_a, msg_a, _ = await capture_service.capture_delivery_by_token(
            delivery.token, courier_a.id
        )
        assert success_a is True, f"שליח A נכשל: {msg_a}"

        # שליח B מנסה לתפוס אותו משלוח — נכשל
        success_b, msg_b, _ = await capture_service.capture_delivery_by_token(
            delivery.token, courier_b.id
        )
        assert success_b is False
        assert "כבר נתפס" in msg_b or "אישור" in msg_b

        # אימות: המשלוח שייך ל-A, ארנק B לא השתנה
        captured = await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.CAPTURED
        )
        assert captured.courier_id == courier_a.id
        await assert_wallet_balance(db_session, courier_a.id, 90.0)
        await assert_wallet_balance(db_session, courier_b.id, 100.0)

    @pytest.mark.asyncio
    async def test_second_request_on_pending_delivery_fails(
        self, db_session, user_factory, wallet_factory, station_factory
    ):
        """משלוח תחנה: שליח A מבקש (PENDING), שליח B נדחה"""
        owner = await user_factory(
            phone_number="+972500000001",
            name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )
        courier_a = await user_factory(
            phone_number="+972502222222",
            name="שליח א",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier_a.id, balance=100.0)

        courier_b = await user_factory(
            phone_number="+972503333333",
            name="שליח ב",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier_b.id, balance=100.0)

        # יצירת משלוח של תחנה
        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
            station_id=station.id,
        )

        # שליח A מבקש — עובר ל-PENDING_APPROVAL
        workflow = ShipmentWorkflowService(db_session)
        success_a, msg_a, _ = await workflow.request_delivery(
            delivery.id, courier_a.id
        )
        assert success_a is True, f"בקשה של שליח A נכשלה: {msg_a}"
        await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.PENDING_APPROVAL
        )

        # שליח B מנסה לבקש — נדחה כי הסטטוס כבר PENDING
        success_b, msg_b, _ = await workflow.request_delivery(
            delivery.id, courier_b.id
        )
        assert success_b is False
        assert "כבר" in msg_b or "ממתין" in msg_b

        # אימות: requesting_courier_id = A
        pending = await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.PENDING_APPROVAL
        )
        assert pending.requesting_courier_id == courier_a.id
