"""
בדיקות הרחבת מערכת Audit Log — פיצ'ר 6

בדיקות שכבת השירות (AuditService):
- רישום שינויי סטטוס משלוח
- רישום אישור/דחיית שליחים
- רישום פעולות ארנק
- שליפת היסטוריית פעולות לפי ישות
- שדות entity_type, entity_id, old_value, new_value

בדיקות מודל:
- action types חדשים קיימים
- station_id nullable
"""
import pytest
from datetime import datetime
from decimal import Decimal

from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_wallet import StationWallet
from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.courier_wallet import CourierWallet
from app.db.models.audit_log import AuditLog, AuditActionType
from app.domain.services.audit_service import AuditService
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.wallet_service import WalletService
from app.domain.services.courier_approval_service import CourierApprovalService


# ============================================================================
# בדיקות מודל — action types חדשים
# ============================================================================


class TestExpandedAuditLogModel:
    """בדיקות מודל AuditLog המורחב"""

    @pytest.mark.unit
    def test_new_action_types_exist(self) -> None:
        """כל סוגי הפעולות החדשים קיימים"""
        new_actions = [
            "courier_approved",
            "courier_rejected",
            "courier_blocked",
            "delivery_status_changed",
            "wallet_debit",
            "wallet_credit",
        ]
        actual_actions = [a.value for a in AuditActionType]
        for expected in new_actions:
            assert expected in actual_actions, f"חסר סוג פעולה חדש: {expected}"

    @pytest.mark.unit
    def test_all_action_types_count(self) -> None:
        """סה״כ 24 סוגי פעולות (11 מקוריים + 6 הרחבה + 7 audit מקיף)"""
        assert len(AuditActionType) == 24

    @pytest.mark.asyncio
    async def test_audit_log_nullable_station_id(self, user_factory, db_session) -> None:
        """station_id יכול להיות None — פעולות שלא קשורות לתחנה"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.ADMIN,
        )
        entry = AuditLog(
            station_id=None,
            actor_user_id=user.id,
            action=AuditActionType.COURIER_APPROVED,
            target_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
        )
        db_session.add(entry)
        await db_session.commit()
        await db_session.refresh(entry)

        assert entry.id is not None
        assert entry.station_id is None

    @pytest.mark.asyncio
    async def test_audit_log_entity_fields(self, user_factory, db_session) -> None:
        """שדות entity_type, entity_id, old_value, new_value נשמרים"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.ADMIN,
        )
        entry = AuditLog(
            actor_user_id=user.id,
            action=AuditActionType.DELIVERY_STATUS_CHANGED,
            entity_type="delivery",
            entity_id=42,
            old_value={"status": "captured"},
            new_value={"status": "delivered"},
        )
        db_session.add(entry)
        await db_session.commit()
        await db_session.refresh(entry)

        assert entry.entity_type == "delivery"
        assert entry.entity_id == 42
        assert entry.old_value == {"status": "captured"}
        assert entry.new_value == {"status": "delivered"}


# ============================================================================
# בדיקות AuditService — שירות מרכזי
# ============================================================================


class TestAuditService:
    """בדיקות שירות audit מרכזי"""

    @pytest.mark.asyncio
    async def test_record_basic_audit(self, user_factory, db_session) -> None:
        """רישום פעולה בסיסית"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.ADMIN,
        )
        audit_service = AuditService(db_session)

        await audit_service.record(
            actor_user_id=user.id,
            action=AuditActionType.COURIER_APPROVED,
            target_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
        )
        await db_session.commit()

        trail = await audit_service.get_entity_audit_trail("user", user.id)
        assert len(trail) == 1
        assert trail[0].action == AuditActionType.COURIER_APPROVED

    @pytest.mark.asyncio
    async def test_record_delivery_status_change(self, user_factory, db_session) -> None:
        """רישום שינוי סטטוס משלוח"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.COURIER,
        )
        audit_service = AuditService(db_session)

        await audit_service.record_delivery_status_change(
            actor_user_id=user.id,
            delivery_id=100,
            old_status="captured",
            new_status="delivered",
            station_id=None,
        )
        await db_session.commit()

        trail = await audit_service.get_entity_audit_trail("delivery", 100)
        assert len(trail) == 1
        entry = trail[0]
        assert entry.old_value == {"status": "captured"}
        assert entry.new_value == {"status": "delivered"}
        assert entry.entity_type == "delivery"
        assert entry.entity_id == 100

    @pytest.mark.asyncio
    async def test_record_courier_approval(self, user_factory, db_session) -> None:
        """רישום אישור שליח"""
        admin = await user_factory(
            phone_number="+972501234567",
            role=UserRole.ADMIN,
        )
        courier = await user_factory(
            phone_number="+972509999999",
            role=UserRole.COURIER,
        )
        audit_service = AuditService(db_session)

        await audit_service.record_courier_approval(
            actor_user_id=admin.id,
            target_user_id=courier.id,
            action=AuditActionType.COURIER_APPROVED,
            old_status="pending",
            new_status="approved",
        )
        await db_session.commit()

        trail = await audit_service.get_entity_audit_trail("user", courier.id)
        assert len(trail) == 1
        assert trail[0].action == AuditActionType.COURIER_APPROVED
        assert trail[0].old_value == {"approval_status": "pending"}
        assert trail[0].new_value == {"approval_status": "approved"}
        assert trail[0].target_user_id == courier.id

    @pytest.mark.asyncio
    async def test_record_wallet_operation(self, user_factory, db_session) -> None:
        """רישום פעולת ארנק"""
        courier = await user_factory(
            phone_number="+972501234567",
            role=UserRole.COURIER,
        )
        audit_service = AuditService(db_session)

        await audit_service.record_wallet_operation(
            actor_user_id=courier.id,
            courier_id=courier.id,
            action=AuditActionType.WALLET_DEBIT,
            amount="-10.00",
            balance_after="-10.00",
            delivery_id=42,
        )
        await db_session.commit()

        trail = await audit_service.get_entity_audit_trail("wallet", courier.id)
        assert len(trail) == 1
        entry = trail[0]
        assert entry.action == AuditActionType.WALLET_DEBIT
        assert entry.new_value == {"amount": "-10.00", "balance_after": "-10.00"}
        assert entry.details["delivery_id"] == 42

    @pytest.mark.asyncio
    async def test_get_audit_logs_with_filters(self, user_factory, db_session) -> None:
        """שליפת לוג ביקורת עם סינון"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.ADMIN,
        )
        audit_service = AuditService(db_session)

        # יצירת 3 רשומות מסוגים שונים
        await audit_service.record(
            actor_user_id=user.id,
            action=AuditActionType.COURIER_APPROVED,
            entity_type="user",
            entity_id=1,
        )
        await audit_service.record(
            actor_user_id=user.id,
            action=AuditActionType.COURIER_REJECTED,
            entity_type="user",
            entity_id=2,
        )
        await audit_service.record(
            actor_user_id=user.id,
            action=AuditActionType.DELIVERY_STATUS_CHANGED,
            entity_type="delivery",
            entity_id=10,
        )
        await db_session.commit()

        # סינון לפי action
        entries, total = await audit_service.get_audit_logs(
            action=AuditActionType.COURIER_APPROVED,
        )
        assert total == 1
        assert entries[0].action == AuditActionType.COURIER_APPROVED

        # סינון לפי entity_type
        entries, total = await audit_service.get_audit_logs(entity_type="user")
        assert total == 2

        # כל הרשומות
        entries, total = await audit_service.get_audit_logs()
        assert total == 3

    @pytest.mark.asyncio
    async def test_get_audit_logs_pagination(self, user_factory, db_session) -> None:
        """pagination עובד נכון"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.ADMIN,
        )
        audit_service = AuditService(db_session)

        # יצירת 5 רשומות
        for i in range(5):
            await audit_service.record(
                actor_user_id=user.id,
                action=AuditActionType.COURIER_APPROVED,
                entity_type="user",
                entity_id=i,
            )
        await db_session.commit()

        # עמוד 1, גודל 2
        entries, total = await audit_service.get_audit_logs(page=1, page_size=2)
        assert total == 5
        assert len(entries) == 2

        # עמוד 3, גודל 2 — רשומה אחת בלבד
        entries, total = await audit_service.get_audit_logs(page=3, page_size=2)
        assert total == 5
        assert len(entries) == 1


# ============================================================================
# בדיקות אינטגרציה — audit ב-DeliveryService
# ============================================================================


class TestDeliveryAuditIntegration:
    """בדיקות אינטגרציה של audit עם שירות משלוחים"""

    async def _create_delivery(
        self,
        db_session,
        sender,
        courier=None,
        status=DeliveryStatus.CAPTURED,
    ) -> Delivery:
        """יצירת משלוח לבדיקה"""
        delivery = Delivery(
            sender_id=sender.id,
            courier_id=courier.id if courier else None,
            pickup_address="רח׳ הבדיקה 1, תל אביב",
            dropoff_address="רח׳ היעד 2, חיפה",
            fee=Decimal("10.00"),
            status=status,
        )
        db_session.add(delivery)
        await db_session.commit()
        await db_session.refresh(delivery)
        return delivery

    @pytest.mark.asyncio
    async def test_mark_delivered_creates_audit(self, user_factory, db_session) -> None:
        """סימון משלוח כנמסר יוצר רשומת audit"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        courier = await user_factory(phone_number="+972502222222", role=UserRole.COURIER)
        delivery = await self._create_delivery(
            db_session, sender, courier, DeliveryStatus.IN_PROGRESS,
        )

        service = DeliveryService(db_session)
        result = await service.mark_delivered(delivery.id)
        assert result is not None
        assert result.status == DeliveryStatus.DELIVERED

        audit_service = AuditService(db_session)
        trail = await audit_service.get_entity_audit_trail("delivery", delivery.id)
        assert len(trail) == 1
        assert trail[0].action == AuditActionType.DELIVERY_STATUS_CHANGED
        assert trail[0].old_value == {"status": "in_progress"}
        assert trail[0].new_value == {"status": "delivered"}

    @pytest.mark.asyncio
    async def test_cancel_delivery_creates_audit(self, user_factory, db_session) -> None:
        """ביטול משלוח יוצר רשומת audit"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        delivery = await self._create_delivery(
            db_session, sender, status=DeliveryStatus.OPEN,
        )

        service = DeliveryService(db_session)
        result = await service.cancel_delivery(delivery.id)
        assert result is not None
        assert result.status == DeliveryStatus.CANCELLED

        audit_service = AuditService(db_session)
        trail = await audit_service.get_entity_audit_trail("delivery", delivery.id)
        assert len(trail) == 1
        assert trail[0].old_value == {"status": "open"}
        assert trail[0].new_value == {"status": "cancelled"}
        assert trail[0].details["reason"] == "ביטול ידני"


# ============================================================================
# בדיקות אינטגרציה — audit ב-CourierApprovalService
# ============================================================================


class TestCourierApprovalAuditIntegration:
    """בדיקות אינטגרציה של audit עם שירות אישור שליחים"""

    @pytest.mark.asyncio
    async def test_approve_creates_audit(self, user_factory, db_session) -> None:
        """אישור שליח יוצר רשומת audit"""
        admin = await user_factory(phone_number="+972501111111", role=UserRole.ADMIN)
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.PENDING,
        )

        result = await CourierApprovalService.approve(
            db_session, courier.id, admin_user_id=admin.id,
        )
        assert result.success is True

        audit_service = AuditService(db_session)
        trail = await audit_service.get_entity_audit_trail("user", courier.id)
        assert len(trail) == 1
        assert trail[0].action == AuditActionType.COURIER_APPROVED
        assert trail[0].old_value == {"approval_status": "pending"}
        assert trail[0].new_value == {"approval_status": "approved"}
        assert trail[0].actor_user_id == admin.id

    @pytest.mark.asyncio
    async def test_reject_creates_audit_with_note(self, user_factory, db_session) -> None:
        """דחיית שליח יוצרת רשומת audit עם הערת דחייה"""
        admin = await user_factory(phone_number="+972501111111", role=UserRole.ADMIN)
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.PENDING,
        )

        result = await CourierApprovalService.reject(
            db_session, courier.id,
            rejection_note="תמונות לא ברורות",
            admin_user_id=admin.id,
        )
        assert result.success is True

        audit_service = AuditService(db_session)
        trail = await audit_service.get_entity_audit_trail("user", courier.id)
        assert len(trail) == 1
        assert trail[0].action == AuditActionType.COURIER_REJECTED
        assert trail[0].details == {"rejection_note": "תמונות לא ברורות"}

    @pytest.mark.asyncio
    async def test_approve_without_admin_id_no_audit(self, user_factory, db_session) -> None:
        """אישור ללא admin_user_id לא יוצר רשומת audit (תאימות לאחור)"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.PENDING,
        )

        result = await CourierApprovalService.approve(db_session, courier.id)
        assert result.success is True

        audit_service = AuditService(db_session)
        trail = await audit_service.get_entity_audit_trail("user", courier.id)
        assert len(trail) == 0


# ============================================================================
# בדיקות אינטגרציה — audit ב-WalletService
# ============================================================================


class TestWalletAuditIntegration:
    """בדיקות אינטגרציה של audit עם שירות ארנק"""

    @pytest.mark.asyncio
    async def test_debit_creates_audit(self, user_factory, db_session) -> None:
        """חיוב ארנק יוצר רשומת audit"""
        courier = await user_factory(phone_number="+972501111111", role=UserRole.COURIER)
        delivery = Delivery(
            sender_id=courier.id,
            pickup_address="בדיקה",
            dropoff_address="בדיקה",
            fee=Decimal("10.00"),
            status=DeliveryStatus.OPEN,
        )
        db_session.add(delivery)
        await db_session.commit()
        await db_session.refresh(delivery)

        wallet_service = WalletService(db_session)
        ledger_entry = await wallet_service.debit_for_capture(
            courier.id, delivery.id, 10.0,
        )
        await db_session.commit()
        assert ledger_entry is not None

        audit_service = AuditService(db_session)
        trail = await audit_service.get_entity_audit_trail("wallet", courier.id)
        assert len(trail) == 1
        assert trail[0].action == AuditActionType.WALLET_DEBIT
        assert Decimal(trail[0].new_value["amount"]) == Decimal("-10")
        assert trail[0].details["delivery_id"] == delivery.id

    @pytest.mark.asyncio
    async def test_credit_creates_audit(self, user_factory, db_session) -> None:
        """זיכוי ארנק יוצר רשומת audit"""
        courier = await user_factory(phone_number="+972501111111", role=UserRole.COURIER)
        delivery = Delivery(
            sender_id=courier.id,
            pickup_address="בדיקה",
            dropoff_address="בדיקה",
            fee=Decimal("10.00"),
            status=DeliveryStatus.DELIVERED,
        )
        db_session.add(delivery)
        await db_session.commit()
        await db_session.refresh(delivery)

        wallet_service = WalletService(db_session)
        ledger_entry = await wallet_service.credit_for_delivery(
            courier.id, delivery.id, 10.0,
        )
        assert ledger_entry is not None

        audit_service = AuditService(db_session)
        trail = await audit_service.get_entity_audit_trail("wallet", courier.id)
        assert len(trail) == 1
        assert trail[0].action == AuditActionType.WALLET_CREDIT
