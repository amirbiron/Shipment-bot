"""
בדיקות שלב 4 - זרימת אישור משלוח וכרטיס סגור

מכסה:
- בקשת משלוח (request_delivery): שליח מאושר, לא מאושר, חסום
- אישור/דחייה (approve/reject): סדרן מורשה, לא מורשה, race condition
- כרטיס סגור: פורמט, מיסוך טלפון
- סינון ברודקאסט: רק שליחים מאושרים
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_blacklist import StationBlacklist
from app.db.models.courier_wallet import CourierWallet
from app.domain.services.shipment_workflow_service import ShipmentWorkflowService
from app.domain.services.capture_service import CaptureService
from app.core.validation import PhoneNumberValidator


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def station_factory(db_session):
    """יצירת תחנת בדיקה"""
    async def _create(
        name: str = "תחנת בדיקה",
        owner_id: int = 1,
        public_group_chat_id: str | None = None,
        private_group_chat_id: str | None = None,
    ) -> Station:
        station = Station(
            name=name,
            owner_id=owner_id,
            public_group_chat_id=public_group_chat_id,
            private_group_chat_id=private_group_chat_id,
            public_group_platform="telegram" if public_group_chat_id else None,
            private_group_platform="telegram" if private_group_chat_id else None,
        )
        db_session.add(station)
        await db_session.commit()
        await db_session.refresh(station)
        return station
    return _create


@pytest.fixture
def dispatcher_factory(db_session):
    """יצירת סדרן לתחנה"""
    async def _create(station_id: int, user_id: int) -> StationDispatcher:
        sd = StationDispatcher(
            station_id=station_id,
            user_id=user_id,
        )
        db_session.add(sd)
        await db_session.commit()
        await db_session.refresh(sd)
        return sd
    return _create


# ============================================================================
# TestDeliveryRequestFlow
# ============================================================================

class TestDeliveryRequestFlow:
    """בדיקות זרימת בקשת משלוח"""

    @pytest.mark.asyncio
    async def test_approved_courier_can_request(
        self, db_session, user_factory, delivery_factory, station_factory,
        dispatcher_factory, wallet_factory
    ):
        """שליח מאושר יכול לבקש משלוח של תחנה"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        courier = await user_factory(
            phone_number="+972502222222", name="שליח מאושר",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)
        sender = await user_factory(
            phone_number="+972501111111", name="שולח"
        )
        delivery = await delivery_factory(
            sender_id=sender.id, fee=10.0
        )
        # עדכון station_id על המשלוח
        delivery.station_id = station.id
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        success, msg, result_delivery = await workflow.request_delivery(
            delivery.id, courier.id
        )

        assert success is True
        assert "נשלחה" in msg
        assert result_delivery.status == DeliveryStatus.PENDING_APPROVAL
        assert result_delivery.requesting_courier_id == courier.id

    @pytest.mark.asyncio
    async def test_unapproved_courier_rejected(
        self, db_session, user_factory, delivery_factory, station_factory
    ):
        """שליח לא מאושר נדחה"""
        owner = await user_factory(
            phone_number="+972500000002", name="בעלים2", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        courier = await user_factory(
            phone_number="+972503333333", name="שליח ממתין",
            role=UserRole.COURIER, approval_status=ApprovalStatus.PENDING,
        )
        sender = await user_factory(
            phone_number="+972501111112", name="שולח2"
        )
        delivery = await delivery_factory(sender_id=sender.id)
        delivery.station_id = station.id
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        success, msg, _ = await workflow.request_delivery(
            delivery.id, courier.id
        )

        assert success is False
        assert "הרשאה" in msg

    @pytest.mark.asyncio
    async def test_blacklisted_courier_rejected(
        self, db_session, user_factory, delivery_factory, station_factory
    ):
        """שליח חסום בתחנה נדחה"""
        owner = await user_factory(
            phone_number="+972500000003", name="בעלים3", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        courier = await user_factory(
            phone_number="+972504444444", name="שליח חסום",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        sender = await user_factory(
            phone_number="+972501111113", name="שולח3"
        )
        delivery = await delivery_factory(sender_id=sender.id)
        delivery.station_id = station.id
        await db_session.commit()

        # הוספה לרשימה שחורה
        blacklist = StationBlacklist(
            station_id=station.id,
            courier_id=courier.id,
            reason="אי תשלום",
        )
        db_session.add(blacklist)
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        success, msg, _ = await workflow.request_delivery(
            delivery.id, courier.id
        )

        assert success is False
        assert "מורשה" in msg or "חסום" in msg

    @pytest.mark.asyncio
    async def test_direct_delivery_bypasses_approval(
        self, db_session, user_factory, delivery_factory, wallet_factory
    ):
        """משלוח ישיר (ללא תחנה) עובר תפיסה ישירה"""
        courier = await user_factory(
            phone_number="+972505555555", name="שליח ישיר",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)
        sender = await user_factory(
            phone_number="+972501111114", name="שולח4"
        )
        delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
        # ללא station_id

        capture_service = CaptureService(db_session)
        success, msg, result = await capture_service.capture_delivery_by_token(
            delivery.token, courier.id
        )

        assert success is True
        assert "נתפס" in msg
        assert result.status == DeliveryStatus.CAPTURED

    @pytest.mark.asyncio
    async def test_request_sets_pending_approval(
        self, db_session, user_factory, delivery_factory, station_factory,
        wallet_factory
    ):
        """בקשה מעדכנת סטטוס ל-PENDING_APPROVAL ושומרת את מזהה השליח"""
        owner = await user_factory(
            phone_number="+972500000004", name="בעלים4", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        courier = await user_factory(
            phone_number="+972506666666", name="שליח5",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)
        sender = await user_factory(
            phone_number="+972501111115", name="שולח5"
        )
        delivery = await delivery_factory(sender_id=sender.id)
        delivery.station_id = station.id
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        await workflow.request_delivery(delivery.id, courier.id)

        await db_session.refresh(delivery)
        assert delivery.status == DeliveryStatus.PENDING_APPROVAL
        assert delivery.requesting_courier_id == courier.id
        assert delivery.requested_at is not None


# ============================================================================
# TestCoordinatorApproval
# ============================================================================

class TestCoordinatorApproval:
    """בדיקות אישור/דחייה על ידי סדרן"""

    @pytest.mark.asyncio
    async def test_approve_captures_and_debits_wallet(
        self, db_session, user_factory, delivery_factory, station_factory,
        dispatcher_factory, wallet_factory
    ):
        """אישור מבצע תפיסה אטומית + חיוב ארנק"""
        owner = await user_factory(
            phone_number="+972500000010", name="בעלים10", role=UserRole.STATION_OWNER
        )
        station = await station_factory(
            owner_id=owner.id,
            private_group_chat_id="-1001234567890",
        )
        dispatcher_user = await user_factory(
            phone_number="+972507777777", name="סדרן1",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await dispatcher_factory(station.id, dispatcher_user.id)

        courier = await user_factory(
            phone_number="+972508888888", name="שליח8",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        wallet = await wallet_factory(courier_id=courier.id, balance=100.0)

        sender = await user_factory(
            phone_number="+972501111116", name="שולח6"
        )
        delivery = await delivery_factory(sender_id=sender.id, fee=15.0)
        delivery.station_id = station.id
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = courier.id
        delivery.requested_at = datetime.now(timezone.utc)
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        success, msg, result = await workflow.approve_delivery(
            delivery.id, dispatcher_user.id
        )

        assert success is True
        assert "אושר" in msg

        await db_session.refresh(delivery)
        assert delivery.status == DeliveryStatus.CAPTURED
        assert delivery.courier_id == courier.id
        assert delivery.approval_decision == "approved"

        await db_session.refresh(wallet)
        assert wallet.balance == 85.0  # 100 - 15

    @pytest.mark.asyncio
    async def test_reject_reverts_to_open(
        self, db_session, user_factory, delivery_factory, station_factory,
        dispatcher_factory
    ):
        """דחייה מחזירה את המשלוח לסטטוס OPEN"""
        owner = await user_factory(
            phone_number="+972500000011", name="בעלים11", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        dispatcher_user = await user_factory(
            phone_number="+972509999999", name="סדרן2",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await dispatcher_factory(station.id, dispatcher_user.id)

        courier = await user_factory(
            phone_number="+972500000099", name="שליח9",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        sender = await user_factory(
            phone_number="+972501111117", name="שולח7"
        )
        delivery = await delivery_factory(sender_id=sender.id)
        delivery.station_id = station.id
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = courier.id
        delivery.requested_at = datetime.now(timezone.utc)
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        success, msg, result = await workflow.reject_delivery(
            delivery.id, dispatcher_user.id
        )

        assert success is True
        assert "נדחה" in msg

        await db_session.refresh(delivery)
        assert delivery.status == DeliveryStatus.OPEN
        assert delivery.requesting_courier_id is None
        assert delivery.approval_decision == "rejected"

    @pytest.mark.asyncio
    async def test_non_dispatcher_cannot_approve(
        self, db_session, user_factory, delivery_factory, station_factory
    ):
        """משתמש שאינו סדרן לא יכול לאשר"""
        owner = await user_factory(
            phone_number="+972500000012", name="בעלים12", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        non_dispatcher = await user_factory(
            phone_number="+972500000098", name="לא סדרן",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        courier = await user_factory(
            phone_number="+972500000097", name="שליח שביקש",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        sender = await user_factory(
            phone_number="+972501111118", name="שולח8"
        )
        delivery = await delivery_factory(sender_id=sender.id)
        delivery.station_id = station.id
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = courier.id
        delivery.requested_at = datetime.now(timezone.utc)
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        success, msg, _ = await workflow.approve_delivery(
            delivery.id, non_dispatcher.id
        )

        assert success is False
        assert "הרשאה" in msg


# ============================================================================
# TestClosedShipmentCard
# ============================================================================

class TestClosedShipmentCard:
    """בדיקות כרטיס משלוח סגור"""

    @pytest.mark.asyncio
    async def test_card_format_approved(self, db_session, user_factory):
        """פורמט כרטיס סגור למשלוח מאושר"""
        courier = await user_factory(
            phone_number="+972501234567", name="יוסי",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        courier.full_name = "יוסי כהן"
        dispatcher = await user_factory(
            phone_number="+972507654321", name="דני",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        dispatcher.full_name = "דני לוי"
        delivery = Delivery(
            id=42,
            sender_id=courier.id,
            pickup_address="רחוב הרצל 1",
            dropoff_address="רחוב בן יהודה 50",
            dropoff_notes="קומה 3",
            fee=25.0,
            status=DeliveryStatus.CAPTURED,
            created_at=datetime(2025, 6, 15, 10, 30),
        )

        card = ShipmentWorkflowService.format_closed_card(
            delivery, courier, "approved", dispatcher
        )

        assert "כרטיס משלוח סגור" in card
        assert "#42" in card
        assert "רחוב הרצל 1" in card
        assert "רחוב בן יהודה 50" in card
        assert "25 ₪" in card
        assert "יוסי כהן" in card
        assert "נשלח לנהג ✅" in card
        assert "דני לוי" in card

    @pytest.mark.asyncio
    async def test_card_format_rejected(self, db_session, user_factory):
        """פורמט כרטיס סגור למשלוח שנדחה"""
        courier = await user_factory(
            phone_number="+972501234568", name="עדי",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        dispatcher = await user_factory(
            phone_number="+972507654322", name="רותם",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        delivery = Delivery(
            id=99,
            sender_id=courier.id,
            pickup_address="כתובת א",
            dropoff_address="כתובת ב",
            fee=30.0,
            status=DeliveryStatus.OPEN,
            created_at=datetime(2025, 7, 1, 14, 0),
        )

        card = ShipmentWorkflowService.format_closed_card(
            delivery, courier, "rejected", dispatcher
        )

        assert "נדחה ❌" in card
        assert "#99" in card

    @pytest.mark.asyncio
    async def test_card_masks_phone(self, db_session, user_factory):
        """מספר טלפון ממוסך בכרטיס סגור"""
        courier = await user_factory(
            phone_number="+972501234567", name="טלפון",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        dispatcher = await user_factory(
            phone_number="+972507654323", name="סדרן",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        delivery = Delivery(
            id=50,
            sender_id=courier.id,
            pickup_address="כתובת",
            dropoff_address="כתובת",
            fee=10.0,
            status=DeliveryStatus.CAPTURED,
            created_at=datetime(2025, 6, 1, 12, 0),
        )

        card = ShipmentWorkflowService.format_closed_card(
            delivery, courier, "approved", dispatcher
        )

        # הטלפון חייב להיות ממוסך — לא מכיל את 4 הספרות האחרונות
        assert "+972501234567" not in card
        masked = PhoneNumberValidator.mask("+972501234567")
        assert masked in card


# ============================================================================
# TestBroadcastFiltering
# ============================================================================

class TestBroadcastFiltering:
    """בדיקות סינון ברודקאסט"""

    @pytest.mark.asyncio
    async def test_excludes_unapproved_couriers(self, db_session, user_factory):
        """ברודקאסט מסנן שליחים לא מאושרים"""
        from app.db.models.outbox_message import MessagePlatform
        from app.workers.tasks import _get_courier_recipients

        # שליח מאושר
        await user_factory(
            phone_number="+972500000020", name="מאושר",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
            platform="whatsapp",
        )
        # שליח ממתין
        await user_factory(
            phone_number="+972500000021", name="ממתין",
            role=UserRole.COURIER, approval_status=ApprovalStatus.PENDING,
            platform="whatsapp",
        )
        # שליח חסום
        await user_factory(
            phone_number="+972500000022", name="חסום",
            role=UserRole.COURIER, approval_status=ApprovalStatus.BLOCKED,
            platform="whatsapp",
        )

        recipients = await _get_courier_recipients(
            db_session, MessagePlatform.WHATSAPP
        )

        assert len(recipients) == 1
        assert recipients[0].name == "מאושר"


# ============================================================================
# TestDeliveryApprovalCommands (WhatsApp)
# ============================================================================

class TestDeliveryApprovalCommands:
    """בדיקות זיהוי פקודות אישור/דחיית משלוח"""

    def test_approve_command(self):
        """זיהוי 'אשר משלוח 123'"""
        from app.api.webhooks.whatsapp import _match_delivery_approval_command

        result = _match_delivery_approval_command("אשר משלוח 123")
        assert result == ("approve", 123)

    def test_reject_command(self):
        """זיהוי 'דחה משלוח 456'"""
        from app.api.webhooks.whatsapp import _match_delivery_approval_command

        result = _match_delivery_approval_command("דחה משלוח 456")
        assert result == ("reject", 456)

    def test_approve_with_emoji(self):
        """זיהוי עם אמוג'י"""
        from app.api.webhooks.whatsapp import _match_delivery_approval_command

        result = _match_delivery_approval_command("✅ אשר משלוח 789")
        assert result == ("approve", 789)

    def test_non_delivery_command_returns_none(self):
        """טקסט שאינו פקודת משלוח מחזיר None"""
        from app.api.webhooks.whatsapp import _match_delivery_approval_command

        result = _match_delivery_approval_command("שלום עולם")
        assert result is None

    def test_courier_approval_not_confused(self):
        """פקודת אישור שליח לא מתבלבלת עם אישור משלוח"""
        from app.api.webhooks.whatsapp import _match_delivery_approval_command

        result = _match_delivery_approval_command("אשר שליח 123")
        assert result is None


# ============================================================================
# TestCaptureRaceCondition (תיקון ריוויו #1)
# ============================================================================

class TestCaptureRaceCondition:
    """בדיקות race condition ב-capture_delivery"""

    @pytest.mark.asyncio
    async def test_capture_pending_wrong_courier_blocked(
        self, db_session, user_factory, delivery_factory, station_factory,
        wallet_factory
    ):
        """שליח אחר לא יכול לתפוס משלוח שממתין לאישור עבור שליח אחר"""
        owner = await user_factory(
            phone_number="+972500000030", name="בעלים30", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        requesting_courier = await user_factory(
            phone_number="+972500000031", name="שליח מבקש",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        stealing_courier = await user_factory(
            phone_number="+972500000032", name="שליח גונב",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=requesting_courier.id, balance=100.0)
        await wallet_factory(courier_id=stealing_courier.id, balance=100.0)
        sender = await user_factory(
            phone_number="+972501111130", name="שולח30"
        )
        delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
        delivery.station_id = station.id
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = requesting_courier.id
        delivery.requested_at = datetime.now(timezone.utc)
        await db_session.commit()

        capture_service = CaptureService(db_session)
        success, msg, _ = await capture_service.capture_delivery(
            delivery.id, stealing_courier.id
        )

        assert success is False
        assert "שליח אחר" in msg

    @pytest.mark.asyncio
    async def test_capture_pending_correct_courier_succeeds(
        self, db_session, user_factory, delivery_factory, station_factory,
        wallet_factory
    ):
        """השליח שביקש יכול להיתפס כשסדרן מאשר"""
        owner = await user_factory(
            phone_number="+972500000033", name="בעלים33", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        courier = await user_factory(
            phone_number="+972500000034", name="שליח נכון",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)
        sender = await user_factory(
            phone_number="+972501111131", name="שולח31"
        )
        delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
        delivery.station_id = station.id
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = courier.id
        delivery.requested_at = datetime.now(timezone.utc)
        await db_session.commit()

        capture_service = CaptureService(db_session)
        success, msg, result = await capture_service.capture_delivery(
            delivery.id, courier.id
        )

        assert success is True
        assert result.status == DeliveryStatus.CAPTURED

    @pytest.mark.asyncio
    async def test_station_delivery_blocked_from_direct_capture(
        self, db_session, user_factory, delivery_factory, station_factory,
        wallet_factory
    ):
        """משלוח של תחנה בסטטוס OPEN לא ניתן לתפיסה ישירה דרך API"""
        owner = await user_factory(
            phone_number="+972500000035", name="בעלים35", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        courier = await user_factory(
            phone_number="+972500000036", name="שליח עוקף",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)
        sender = await user_factory(
            phone_number="+972501111132", name="שולח32"
        )
        delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
        delivery.station_id = station.id
        # סטטוס OPEN — ניסיון תפיסה ישירה צריך להיחסם
        await db_session.commit()

        capture_service = CaptureService(db_session)
        success, msg, _ = await capture_service.capture_delivery(
            delivery.id, courier.id
        )

        assert success is False
        assert "אישור סדרן" in msg


# ============================================================================
# TestDispatcherScoping (תיקון ריוויו #3)
# ============================================================================

class TestDispatcherScoping:
    """בדיקות scoping של סדרן לתחנה ספציפית"""

    @pytest.mark.asyncio
    async def test_is_dispatcher_of_station_correct(
        self, db_session, user_factory, station_factory, dispatcher_factory
    ):
        """סדרן מזוהה בתחנה שלו"""
        from app.domain.services.station_service import StationService

        owner = await user_factory(
            phone_number="+972500000040", name="בעלים40", role=UserRole.STATION_OWNER
        )
        station = await station_factory(owner_id=owner.id)
        disp_user = await user_factory(
            phone_number="+972500000041", name="סדרן41",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await dispatcher_factory(station.id, disp_user.id)

        service = StationService(db_session)
        assert await service.is_dispatcher_of_station(disp_user.id, station.id) is True

    @pytest.mark.asyncio
    async def test_is_dispatcher_of_wrong_station(
        self, db_session, user_factory, station_factory, dispatcher_factory
    ):
        """סדרן של תחנה A לא מורשה בתחנה B"""
        from app.domain.services.station_service import StationService

        owner = await user_factory(
            phone_number="+972500000042", name="בעלים42", role=UserRole.STATION_OWNER
        )
        station_a = await station_factory(name="תחנה A", owner_id=owner.id)
        station_b = await station_factory(name="תחנה B", owner_id=owner.id)
        disp_user = await user_factory(
            phone_number="+972500000043", name="סדרן43",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        # סדרן רק בתחנה A
        await dispatcher_factory(station_a.id, disp_user.id)

        service = StationService(db_session)
        assert await service.is_dispatcher_of_station(disp_user.id, station_a.id) is True
        assert await service.is_dispatcher_of_station(disp_user.id, station_b.id) is False

    @pytest.mark.asyncio
    async def test_cross_station_approve_blocked(
        self, db_session, user_factory, delivery_factory, station_factory,
        dispatcher_factory
    ):
        """סדרן של תחנה A לא יכול לאשר משלוח של תחנה B"""
        owner = await user_factory(
            phone_number="+972500000044", name="בעלים44", role=UserRole.STATION_OWNER
        )
        station_a = await station_factory(name="תחנה A2", owner_id=owner.id)
        station_b = await station_factory(name="תחנה B2", owner_id=owner.id)
        disp_user = await user_factory(
            phone_number="+972500000045", name="סדרן45",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await dispatcher_factory(station_a.id, disp_user.id)

        courier = await user_factory(
            phone_number="+972500000046", name="שליח46",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        sender = await user_factory(
            phone_number="+972501111140", name="שולח40"
        )
        delivery = await delivery_factory(sender_id=sender.id)
        # משלוח של תחנה B
        delivery.station_id = station_b.id
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = courier.id
        delivery.requested_at = datetime.now(timezone.utc)
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        success, msg, _ = await workflow.approve_delivery(
            delivery.id, disp_user.id
        )

        assert success is False
        assert "הרשאה" in msg


# ============================================================================
# TestMultiStationDispatcher (תיקון ריוויו #4)
# ============================================================================

class TestMultiStationDispatcher:
    """בדיקות סדרן מרובה-תחנות — is_dispatcher_of_station במקום get_dispatcher_station"""

    @pytest.mark.asyncio
    async def test_multi_station_dispatcher_approve_correct_station(
        self, db_session, user_factory, delivery_factory, station_factory,
        dispatcher_factory, wallet_factory
    ):
        """סדרן המשויך לשתי תחנות יכול לאשר משלוח בתחנה השנייה"""
        owner = await user_factory(
            phone_number="+972500000050", name="בעלים50", role=UserRole.STATION_OWNER
        )
        station_a = await station_factory(name="תחנה A3", owner_id=owner.id)
        station_b = await station_factory(name="תחנה B3", owner_id=owner.id)
        disp_user = await user_factory(
            phone_number="+972500000051", name="סדרן51",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        # סדרן בשתי התחנות
        await dispatcher_factory(station_a.id, disp_user.id)
        await dispatcher_factory(station_b.id, disp_user.id)

        courier = await user_factory(
            phone_number="+972500000052", name="שליח52",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)
        sender = await user_factory(
            phone_number="+972501111150", name="שולח50"
        )
        # משלוח בתחנה B (לא הראשונה שתחזור מ-limit(1))
        delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
        delivery.station_id = station_b.id
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = courier.id
        delivery.requested_at = datetime.now(timezone.utc)
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        success, msg, result = await workflow.approve_delivery(
            delivery.id, disp_user.id
        )

        assert success is True
        assert result.status == DeliveryStatus.CAPTURED

    @pytest.mark.asyncio
    async def test_multi_station_dispatcher_reject_correct_station(
        self, db_session, user_factory, delivery_factory, station_factory,
        dispatcher_factory
    ):
        """סדרן המשויך לשתי תחנות יכול לדחות משלוח בתחנה השנייה"""
        owner = await user_factory(
            phone_number="+972500000053", name="בעלים53", role=UserRole.STATION_OWNER
        )
        station_a = await station_factory(name="תחנה A4", owner_id=owner.id)
        station_b = await station_factory(name="תחנה B4", owner_id=owner.id)
        disp_user = await user_factory(
            phone_number="+972500000054", name="סדרן54",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        await dispatcher_factory(station_a.id, disp_user.id)
        await dispatcher_factory(station_b.id, disp_user.id)

        courier = await user_factory(
            phone_number="+972500000055", name="שליח55",
            role=UserRole.COURIER, approval_status=ApprovalStatus.APPROVED,
        )
        sender = await user_factory(
            phone_number="+972501111151", name="שולח51"
        )
        delivery = await delivery_factory(sender_id=sender.id)
        delivery.station_id = station_b.id
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = courier.id
        delivery.requested_at = datetime.now(timezone.utc)
        await db_session.commit()

        workflow = ShipmentWorkflowService(db_session)
        success, msg, result = await workflow.reject_delivery(
            delivery.id, disp_user.id
        )

        assert success is True
        assert result.status == DeliveryStatus.OPEN


# ============================================================================
# TestWhatsAppDeliveryApprovalFeedback (תיקון ריוויו #4)
# ============================================================================

class TestWhatsAppDeliveryApprovalFeedback:
    """בדיקות הודעות שגיאה בוואטסאפ לפקודות אישור משלוח"""

    def test_match_delivery_approval_nonexistent_delivery(self):
        """פקודת אישור עם מספר משלוח מזוהה כפקודה"""
        from app.api.webhooks.whatsapp import _match_delivery_approval_command

        result = _match_delivery_approval_command("אשר משלוח 99999")
        assert result == ("approve", 99999)

    def test_match_delivery_rejection_command(self):
        """פקודת דחייה מזוהה כפקודה"""
        from app.api.webhooks.whatsapp import _match_delivery_approval_command

        result = _match_delivery_approval_command("דחה משלוח 456")
        assert result == ("reject", 456)
