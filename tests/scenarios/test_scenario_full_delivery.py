"""
תרחיש 1 — Happy Path: מחזור חיי משלוח מלא

מכסה:
- יצירת משלוח ישיר (ללא תחנה) → תפיסה → סימון כנמסר
- שידור outbox בעת יצירה ותפיסה
- חיוב ארנק שליח + רשומת ledger
- כשלון תפיסה באשראי לא מספיק
"""
import pytest

from app.db.models.user import UserRole, ApprovalStatus
from app.db.models.delivery import DeliveryStatus
from app.db.models.wallet_ledger import LedgerEntryType
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.capture_service import CaptureService

from tests.scenarios.conftest import (
    assert_delivery_status,
    assert_outbox_count,
    assert_wallet_balance,
    assert_ledger_count,
)


@pytest.mark.scenario
class TestFullDeliveryLifecycle:
    """מחזור חיי משלוח מלא — מיצירה ועד מסירה"""

    @pytest.mark.asyncio
    async def test_create_capture_deliver(
        self, db_session, user_factory, wallet_factory
    ):
        """יצירת משלוח → תפיסה ישירה → סימון כנמסר — happy path מלא"""
        # --- הכנה ---
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח בדיקה",
            role=UserRole.SENDER,
            telegram_chat_id="11111",
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח בדיקה",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
            telegram_chat_id="22222",
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        # --- שלב 1: יצירת משלוח ---
        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=15.0,
        )
        assert delivery.id is not None
        assert delivery.token is not None

        # אימות: סטטוס OPEN + שידור outbox
        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.OPEN)
        await assert_outbox_count(db_session, "delivery_broadcast", min_count=1)

        # --- שלב 2: תפיסה ישירה דרך token ---
        capture_service = CaptureService(db_session)
        success, msg, captured = await capture_service.capture_delivery_by_token(
            delivery.token, courier.id
        )
        assert success is True, f"תפיסה נכשלה: {msg}"

        # אימות: סטטוס CAPTURED + ארנק חויב + ledger
        captured_delivery = await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.CAPTURED
        )
        assert captured_delivery.courier_id == courier.id
        assert captured_delivery.captured_at is not None

        await assert_wallet_balance(db_session, courier.id, 85.0)
        await assert_ledger_count(db_session, courier.id, 1)

        # אימות: הודעת capture נשלחה לשולח
        await assert_outbox_count(db_session, "capture_notification_sender", min_count=1)

        # --- שלב 3: סימון כנמסר ---
        result = await delivery_service.mark_delivered(delivery.id)
        assert result is not None

        delivered = await assert_delivery_status(
            db_session, delivery.id, DeliveryStatus.DELIVERED
        )
        assert delivered.delivered_at is not None

    @pytest.mark.asyncio
    async def test_capture_insufficient_credit_fails(
        self, db_session, user_factory, wallet_factory
    ):
        """תפיסה עם אשראי לא מספיק — נכשלת בלי לשנות סטטוס"""
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
        # ארנק עם מגבלת אשראי 0 — לא יכול להיות ביתרה שלילית
        await wallet_factory(courier_id=courier.id, balance=0.0, credit_limit=0.0)

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
        )

        capture_service = CaptureService(db_session)
        success, msg, _ = await capture_service.capture_delivery_by_token(
            delivery.token, courier.id
        )
        assert success is False
        assert "יתרה" in msg

        # אימות: סטטוס לא השתנה + ארנק לא חויב
        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.OPEN)
        await assert_wallet_balance(db_session, courier.id, 0.0)
        await assert_ledger_count(db_session, courier.id, 0)
