"""
תרחיש 3 — ביטול זרימה: ביטול משלוח, שחרור אחרי תפיסה

מכסה:
- ביטול משלוח פתוח — סטטוס CANCELLED, תפיסה נכשלת
- ביטול אחרי תפיסה — נדחה (cancel רק על OPEN)
- שחרור (release) — החזרת ארנק + חזרה ל-OPEN
"""
import pytest

from app.db.models.user import UserRole, ApprovalStatus
from app.db.models.delivery import DeliveryStatus
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.capture_service import CaptureService

from tests.scenarios.conftest import (
    assert_delivery_status,
    assert_wallet_balance,
    assert_ledger_count,
)


@pytest.mark.scenario
class TestFlowCancellation:
    """ביטול זרימה — ביטול משלוח ושחרור תפיסה"""

    @pytest.mark.asyncio
    async def test_cancel_open_delivery_then_capture_fails(
        self, db_session, user_factory, wallet_factory
    ):
        """ביטול משלוח OPEN ואז ניסיון תפיסה — נכשל"""
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="11111",
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
        )

        # ביטול
        cancelled = await delivery_service.cancel_delivery(delivery.id)
        assert cancelled is not None
        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.CANCELLED)

        # ניסיון תפיסה אחרי ביטול — נכשל
        capture_service = CaptureService(db_session)
        success, msg, _ = await capture_service.capture_delivery_by_token(
            delivery.token, courier.id
        )
        assert success is False

    @pytest.mark.asyncio
    async def test_cancel_after_capture_not_allowed(
        self, db_session, user_factory, wallet_factory
    ):
        """ביטול אחרי תפיסה — נדחה (cancel עובד רק על OPEN)"""
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="11111",
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
        )

        # תפיסה
        capture_service = CaptureService(db_session)
        success, _, _ = await capture_service.capture_delivery_by_token(
            delivery.token, courier.id
        )
        assert success is True
        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.CAPTURED)

        # ניסיון ביטול — cancel_delivery מחזיר את המשלוח כמו שהוא (לא משנה סטטוס)
        result = await delivery_service.cancel_delivery(delivery.id)
        # cancel_delivery לא משנה סטטוס אם לא OPEN
        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.CAPTURED)

    @pytest.mark.asyncio
    async def test_release_restores_wallet_and_status(
        self, db_session, user_factory, wallet_factory
    ):
        """שחרור משלוח — מחזיר ארנק + סטטוס OPEN"""
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="11111",
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
        )

        # תפיסה — ארנק יורד ל-90
        capture_service = CaptureService(db_session)
        success, _, _ = await capture_service.capture_delivery_by_token(
            delivery.token, courier.id
        )
        assert success is True
        await assert_wallet_balance(db_session, courier.id, 90.0)
        await assert_ledger_count(db_session, courier.id, 1)

        # שחרור — ארנק חוזר ל-100, סטטוס חוזר ל-OPEN
        release_ok, release_msg = await capture_service.release_delivery(
            delivery.id, courier.id
        )
        assert release_ok is True, f"שחרור נכשל: {release_msg}"

        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.OPEN)
        await assert_wallet_balance(db_session, courier.id, 100.0)
        # 2 רשומות: debit + refund
        await assert_ledger_count(db_session, courier.id, 2)
