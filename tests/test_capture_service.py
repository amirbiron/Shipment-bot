"""
בדיקות יחידה ל-CaptureService — לוגיקת תפיסת משלוח אטומית
"""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.courier_wallet import CourierWallet
from app.db.models.wallet_ledger import WalletLedger, LedgerEntryType
from app.db.models.user import UserRole
from app.domain.services.capture_service import CaptureService, CaptureError


@pytest.mark.unit
class TestCaptureDelivery:
    """בדיקות תפיסת משלוח ישירה"""

    async def test_capture_open_delivery_success(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """תפיסת משלוח פתוח עם יתרה מספקת — צריכה להצליח"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
        await wallet_factory(courier_id=courier.id, balance=100.0, credit_limit=-500.0)

        service = CaptureService(db_session)
        with patch.object(service.outbox_service, "queue_capture_notification", new_callable=AsyncMock):
            success, msg, result_delivery = await service.capture_delivery(delivery.id, courier.id)

        assert success is True
        assert result_delivery is not None
        assert result_delivery.status == DeliveryStatus.CAPTURED
        assert result_delivery.courier_id == courier.id
        assert "נתפס בהצלחה" in msg

    async def test_capture_creates_wallet_if_not_exists(
        self, db_session, user_factory, delivery_factory
    ):
        """תפיסה צריכה ליצור ארנק אם לא קיים"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        # עמלה 0 כדי שלא ייכשל על יתרה
        delivery = await delivery_factory(sender_id=sender.id, fee=0.0)

        service = CaptureService(db_session)
        with patch.object(service.outbox_service, "queue_capture_notification", new_callable=AsyncMock):
            success, msg, _ = await service.capture_delivery(delivery.id, courier.id)

        assert success is True

    async def test_capture_nonexistent_delivery(self, db_session):
        """תפיסת משלוח שלא קיים — צריכה להיכשל"""
        service = CaptureService(db_session)
        success, msg, delivery = await service.capture_delivery(99999, 1)

        assert success is False
        assert "לא נמצא" in msg
        assert delivery is None

    async def test_capture_already_captured_delivery(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """תפיסת משלוח שכבר נתפס — צריכה להיכשל"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier1 = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        courier2 = await user_factory(phone_number="+972503333333", role=UserRole.COURIER)
        delivery = await delivery_factory(
            sender_id=sender.id, status=DeliveryStatus.CAPTURED, courier_id=courier1.id
        )
        await wallet_factory(courier_id=courier2.id, balance=100.0)

        service = CaptureService(db_session)
        success, msg, _ = await service.capture_delivery(delivery.id, courier2.id)

        assert success is False
        assert "כבר נתפס" in msg

    async def test_capture_insufficient_credit(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """תפיסה עם יתרה לא מספיקה — צריכה להיכשל"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(sender_id=sender.id, fee=200.0)
        # מגבלת אשראי -100, יתרה 0 → balance אחרי עמלה: -200 < -100
        await wallet_factory(courier_id=courier.id, balance=0.0, credit_limit=-100.0)

        service = CaptureService(db_session)
        success, msg, _ = await service.capture_delivery(delivery.id, courier.id)

        assert success is False
        assert "יתרה לא מספיקה" in msg

    async def test_capture_station_delivery_requires_approval(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """משלוח של תחנה בסטטוס OPEN — צריך לעבור דרך אישור סדרן"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        # יצירת משלוח עם station_id ישיר (בלי ליצור תחנה אמיתית)
        delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
        delivery.station_id = 1
        await db_session.commit()
        await wallet_factory(courier_id=courier.id, balance=100.0)

        service = CaptureService(db_session)
        success, msg, _ = await service.capture_delivery(delivery.id, courier.id)

        assert success is False
        assert "אישור סדרן" in msg

    async def test_capture_pending_wrong_courier(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """משלוח ב-PENDING_APPROVAL עבור שליח אחר — צריך להיכשל"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier1 = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        courier2 = await user_factory(phone_number="+972503333333", role=UserRole.COURIER)
        delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = courier1.id
        await db_session.commit()
        await wallet_factory(courier_id=courier2.id, balance=100.0)

        service = CaptureService(db_session)
        success, msg, _ = await service.capture_delivery(delivery.id, courier2.id)

        assert success is False
        assert "שליח אחר" in msg

    async def test_capture_creates_ledger_entry(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """תפיסה צריכה ליצור רשומת ledger"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(sender_id=sender.id, fee=15.0)
        await wallet_factory(courier_id=courier.id, balance=100.0, credit_limit=-500.0)

        service = CaptureService(db_session)
        with patch.object(service.outbox_service, "queue_capture_notification", new_callable=AsyncMock):
            await service.capture_delivery(delivery.id, courier.id)

        from sqlalchemy import select
        result = await db_session.execute(
            select(WalletLedger).where(
                WalletLedger.courier_id == courier.id,
                WalletLedger.delivery_id == delivery.id,
            )
        )
        ledger = result.scalar_one()
        assert ledger.entry_type == LedgerEntryType.DELIVERY_FEE_DEBIT
        assert ledger.amount == Decimal("-15.00")
        assert ledger.balance_after == Decimal("85.00")

    async def test_capture_updates_wallet_balance(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """תפיסה צריכה לעדכן את יתרת הארנק"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(sender_id=sender.id, fee=25.0)
        wallet = await wallet_factory(courier_id=courier.id, balance=100.0, credit_limit=-500.0)

        service = CaptureService(db_session)
        with patch.object(service.outbox_service, "queue_capture_notification", new_callable=AsyncMock):
            await service.capture_delivery(delivery.id, courier.id)

        from sqlalchemy import select
        result = await db_session.execute(
            select(CourierWallet).where(CourierWallet.courier_id == courier.id)
        )
        updated_wallet = result.scalar_one()
        assert updated_wallet.balance == Decimal("75.00")

    async def test_capture_no_commit_when_auto_commit_false(
        self, db_session, user_factory, delivery_factory, wallet_factory, monkeypatch
    ):
        """כאשר auto_commit=False — אסור לעשות commit (הקורא מנהל את הטרנזקציה)"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(sender_id=sender.id, fee=5.0)
        await wallet_factory(courier_id=courier.id, balance=100.0, credit_limit=-500.0)

        service = CaptureService(db_session)
        commit_mock = AsyncMock()
        monkeypatch.setattr(db_session, "commit", commit_mock)

        with patch.object(service.outbox_service, "queue_capture_notification", new_callable=AsyncMock):
            success, _, _ = await service.capture_delivery(
                delivery.id, courier.id, auto_commit=False
            )

        assert success is True
        commit_mock.assert_not_awaited()


@pytest.mark.unit
class TestCaptureDeliveryByToken:
    """בדיקות תפיסה לפי טוקן"""

    async def test_capture_by_invalid_token(self, db_session):
        """טוקן לא תקין — צריך להיכשל"""
        service = CaptureService(db_session)
        success, msg, delivery = await service.capture_delivery_by_token("invalid-token", 1)

        assert success is False
        assert "לא תקין" in msg

    async def test_capture_by_token_station_routes_to_workflow(
        self, db_session, user_factory, delivery_factory
    ):
        """משלוח של תחנה — צריך לעבור דרך workflow"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(sender_id=sender.id)
        delivery.station_id = 1
        await db_session.commit()
        await db_session.refresh(delivery)

        service = CaptureService(db_session)
        mock_workflow = AsyncMock()
        mock_workflow.request_delivery = AsyncMock(return_value=(True, "הבקשה נשלחה", delivery))
        mock_workflow_cls = MagicMock(return_value=mock_workflow)

        with patch(
            "app.domain.services.shipment_workflow_service.ShipmentWorkflowService",
            mock_workflow_cls,
        ):
            success, msg, _ = await service.capture_delivery_by_token(delivery.token, courier.id)

        assert success is True
        mock_workflow.request_delivery.assert_awaited_once_with(delivery.id, courier.id)

    async def test_capture_by_token_direct_delivery(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """משלוח ישיר (ללא תחנה) — תפיסה ישירה"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(sender_id=sender.id, fee=5.0)
        await wallet_factory(courier_id=courier.id, balance=100.0, credit_limit=-500.0)

        service = CaptureService(db_session)
        with patch.object(service.outbox_service, "queue_capture_notification", new_callable=AsyncMock):
            success, msg, result = await service.capture_delivery_by_token(
                delivery.token, courier.id
            )

        assert success is True
        assert result.status == DeliveryStatus.CAPTURED


@pytest.mark.unit
class TestReleaseDelivery:
    """בדיקות שחרור משלוח"""

    async def test_release_captured_delivery(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """שחרור משלוח שנתפס — צריך להחזיר עמלה ולפתוח מחדש"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.CAPTURED,
            courier_id=courier.id,
            fee=20.0,
        )
        await wallet_factory(courier_id=courier.id, balance=80.0, credit_limit=-500.0)

        service = CaptureService(db_session)
        success, msg = await service.release_delivery(delivery.id, courier.id)

        assert success is True
        assert "שוחרר" in msg

        # וידוא שהמשלוח חזר ל-OPEN
        from sqlalchemy import select
        result = await db_session.execute(
            select(Delivery).where(Delivery.id == delivery.id)
        )
        updated = result.scalar_one()
        assert updated.status == DeliveryStatus.OPEN
        assert updated.courier_id is None

    async def test_release_refunds_wallet(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """שחרור צריך לזכות את הארנק"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.CAPTURED,
            courier_id=courier.id,
            fee=30.0,
        )
        await wallet_factory(courier_id=courier.id, balance=70.0, credit_limit=-500.0)

        service = CaptureService(db_session)
        await service.release_delivery(delivery.id, courier.id)

        from sqlalchemy import select
        result = await db_session.execute(
            select(CourierWallet).where(CourierWallet.courier_id == courier.id)
        )
        wallet = result.scalar_one()
        assert wallet.balance == Decimal("100.00")

    async def test_release_wrong_courier(
        self, db_session, user_factory, delivery_factory
    ):
        """שחרור ע"י שליח אחר — צריך להיכשל"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier1 = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        courier2 = await user_factory(phone_number="+972503333333", role=UserRole.COURIER)
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.CAPTURED,
            courier_id=courier1.id,
        )

        service = CaptureService(db_session)
        success, msg = await service.release_delivery(delivery.id, courier2.id)

        assert success is False
        assert "לא שייך" in msg

    async def test_release_non_captured_delivery(
        self, db_session, user_factory, delivery_factory
    ):
        """שחרור משלוח שלא בסטטוס CAPTURED — צריך להיכשל"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.DELIVERED,
            courier_id=courier.id,
        )

        service = CaptureService(db_session)
        success, msg = await service.release_delivery(delivery.id, courier.id)

        assert success is False
        assert "לא ניתן" in msg

    async def test_release_nonexistent_delivery(self, db_session):
        """שחרור משלוח שלא קיים — צריך להיכשל"""
        service = CaptureService(db_session)
        success, msg = await service.release_delivery(99999, 1)

        assert success is False
        assert "לא נמצא" in msg
