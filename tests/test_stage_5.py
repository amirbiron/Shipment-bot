"""
בדיקות שלב 5 - מדיניות פיננסית וחסימה אוטומטית

מכסה:
- זיכוי עמלת תחנה (10%) בסימון משלוח כנמסר
- דוח גבייה - סינון חיובים ששולמו
- חסימה אוטומטית של נהגים שלא שילמו חודשיים רצופים
- זיהוי אוטומטי של courier_id בחיוב ידני
- חישוב מחזורי חיוב
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType
from app.db.models.station_blacklist import StationBlacklist
from app.db.models.manual_charge import ManualCharge
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.station_service import StationService


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def station_factory(db_session):
    """יצירת תחנת בדיקה"""
    async def _create(
        name: str = "תחנת בדיקה",
        owner_id: int = 1,
    ) -> Station:
        station = Station(name=name, owner_id=owner_id)
        db_session.add(station)
        await db_session.commit()
        await db_session.refresh(station)
        return station
    return _create


@pytest.fixture
def station_wallet_factory(db_session):
    """יצירת ארנק תחנה"""
    async def _create(
        station_id: int,
        balance: float = 0.0,
        commission_rate: float = 0.10,
    ) -> StationWallet:
        wallet = StationWallet(
            station_id=station_id,
            balance=balance,
            commission_rate=commission_rate,
        )
        db_session.add(wallet)
        await db_session.commit()
        await db_session.refresh(wallet)
        return wallet
    return _create


@pytest.fixture
def manual_charge_factory(db_session):
    """יצירת חיוב ידני לבדיקות"""
    async def _create(
        station_id: int,
        dispatcher_id: int,
        driver_name: str = "נהג בדיקה",
        amount: float = 50.0,
        courier_id: int | None = None,
        is_paid: bool = False,
        created_at: datetime | None = None,
    ) -> ManualCharge:
        charge = ManualCharge(
            station_id=station_id,
            dispatcher_id=dispatcher_id,
            driver_name=driver_name,
            amount=amount,
            courier_id=courier_id,
            is_paid=is_paid,
        )
        if created_at:
            charge.created_at = created_at
        db_session.add(charge)
        await db_session.commit()
        await db_session.refresh(charge)
        return charge
    return _create


# ============================================================================
# TestCommissionOnDelivery - זיכוי עמלת תחנה
# ============================================================================

class TestCommissionOnDelivery:
    """בדיקות זיכוי עמלת תחנה (10%) בסימון משלוח כנמסר"""

    @pytest.mark.asyncio
    async def test_mark_delivered_credits_station_commission(
        self, db_session, user_factory, delivery_factory,
        station_factory, station_wallet_factory
    ):
        """משלוח שייך לתחנה → סימון כנמסר מזכה 10% עמלה בארנק תחנה"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        wallet = await station_wallet_factory(
            station_id=station.id, balance=0.0, commission_rate=0.10
        )
        sender = await user_factory(
            phone_number="+972501111111", name="שולח"
        )
        delivery = await delivery_factory(
            sender_id=sender.id, fee=100.0,
            status=DeliveryStatus.CAPTURED,
        )
        # שיוך לתחנה
        delivery.station_id = station.id
        await db_session.commit()

        service = DeliveryService(db_session)
        result = await service.mark_delivered(delivery.id)

        assert result is not None
        assert result.status == DeliveryStatus.DELIVERED
        assert result.delivered_at is not None

        # וידוא שהארנק עודכן ב-10%
        await db_session.refresh(wallet)
        assert wallet.balance == pytest.approx(10.0)  # 10% של 100

        # וידוא רישום בלדג'ר
        from sqlalchemy import select
        ledger_result = await db_session.execute(
            select(StationLedger).where(
                StationLedger.station_id == station.id,
                StationLedger.entry_type == StationLedgerEntryType.COMMISSION_CREDIT,
            )
        )
        entry = ledger_result.scalar_one_or_none()
        assert entry is not None
        assert entry.amount == pytest.approx(10.0)
        assert entry.delivery_id == delivery.id

    @pytest.mark.asyncio
    async def test_mark_delivered_without_station_no_commission(
        self, db_session, user_factory, delivery_factory
    ):
        """משלוח ללא תחנה → סימון כנמסר ללא זיכוי עמלה"""
        sender = await user_factory(
            phone_number="+972501111111", name="שולח"
        )
        delivery = await delivery_factory(
            sender_id=sender.id, fee=100.0,
            status=DeliveryStatus.CAPTURED,
        )

        service = DeliveryService(db_session)
        result = await service.mark_delivered(delivery.id)

        assert result is not None
        assert result.status == DeliveryStatus.DELIVERED

        # וידוא שלא נוצרו רשומות בלדג'ר
        from sqlalchemy import select
        ledger_result = await db_session.execute(select(StationLedger))
        assert ledger_result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_mark_delivered_from_in_progress_status(
        self, db_session, user_factory, delivery_factory
    ):
        """משלוח בסטטוס IN_PROGRESS → ניתן לסמן כנמסר"""
        sender = await user_factory(
            phone_number="+972501111111", name="שולח"
        )
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.IN_PROGRESS,
        )

        service = DeliveryService(db_session)
        result = await service.mark_delivered(delivery.id)

        assert result is not None
        assert result.status == DeliveryStatus.DELIVERED

    @pytest.mark.asyncio
    async def test_mark_delivered_rejects_wrong_status(
        self, db_session, user_factory, delivery_factory
    ):
        """משלוח בסטטוס OPEN → לא ניתן לסמן כנמסר"""
        sender = await user_factory(
            phone_number="+972501111111", name="שולח"
        )
        delivery = await delivery_factory(
            sender_id=sender.id,
            status=DeliveryStatus.OPEN,
        )

        service = DeliveryService(db_session)
        result = await service.mark_delivered(delivery.id)

        assert result is None  # מעבר סטטוס לא תקין

    @pytest.mark.asyncio
    async def test_mark_delivered_nonexistent_delivery(
        self, db_session
    ):
        """משלוח לא קיים → מחזיר None"""
        service = DeliveryService(db_session)
        result = await service.mark_delivered(99999)
        assert result is None


# ============================================================================
# TestCollectionReport - דוח גבייה
# ============================================================================

class TestCollectionReport:
    """בדיקות דוח גבייה"""

    @pytest.mark.asyncio
    async def test_collection_report_excludes_paid_charges(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """דוח גבייה לא כולל חיובים ששולמו"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        await station_wallet_factory(station_id=station.id)
        dispatcher = await user_factory(
            phone_number="+972500000002", name="סדרן",
        )

        # חיוב ששולם
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג ששילם",
            amount=100.0,
            is_paid=True,
        )
        # חיוב שלא שולם
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג שלא שילם",
            amount=200.0,
            is_paid=False,
        )

        service = StationService(db_session)
        report = await service.get_collection_report(station.id)

        # רק חיוב אחד בדוח (שלא שולם)
        assert len(report) == 1
        assert report[0]["driver_name"] == "נהג שלא שילם"
        assert report[0]["total_debt"] == pytest.approx(200.0)


# ============================================================================
# TestAutoBlocking - חסימה אוטומטית
# ============================================================================

class TestAutoBlocking:
    """בדיקות חסימה אוטומטית של נהגים שלא שילמו חודשיים רצופים"""

    @pytest.mark.asyncio
    async def test_auto_block_with_two_consecutive_unpaid_months(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """נהג עם חיובים שלא שולמו ב-2 מחזורים רצופים → נחסם אוטומטית"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        await station_wallet_factory(station_id=station.id)
        dispatcher = await user_factory(
            phone_number="+972500000002", name="סדרן",
        )
        courier = await user_factory(
            phone_number="+972502222222", name="נהג חייב",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        service = StationService(db_session)

        # חישוב תאריכים — נוצרים חיובים בשני מחזורים
        current_cycle = service.get_billing_cycle_start()
        previous_cycle = service._get_previous_billing_cycle_start()

        # חיוב במחזור הקודם
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג חייב",
            amount=100.0,
            courier_id=courier.id,
            created_at=previous_cycle + timedelta(days=1),
        )
        # חיוב במחזור הנוכחי
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג חייב",
            amount=150.0,
            courier_id=courier.id,
            created_at=current_cycle + timedelta(days=1),
        )

        blocked = await service.auto_block_unpaid_drivers(station.id)

        assert len(blocked) == 1
        assert blocked[0]["courier_id"] == courier.id
        assert blocked[0]["driver_name"] == "נהג חייב"

        # וידוא שהנהג ברשימה השחורה
        assert await service.is_blacklisted(station.id, courier.id) is True

    @pytest.mark.asyncio
    async def test_no_block_with_only_one_month_unpaid(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """חיובים רק במחזור אחד → לא חוסם"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        await station_wallet_factory(station_id=station.id)
        dispatcher = await user_factory(
            phone_number="+972500000002", name="סדרן",
        )
        courier = await user_factory(
            phone_number="+972502222222", name="נהג",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        service = StationService(db_session)
        current_cycle = service.get_billing_cycle_start()

        # חיוב רק במחזור הנוכחי
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג",
            amount=100.0,
            courier_id=courier.id,
            created_at=current_cycle + timedelta(days=1),
        )

        blocked = await service.auto_block_unpaid_drivers(station.id)
        assert len(blocked) == 0
        assert await service.is_blacklisted(station.id, courier.id) is False

    @pytest.mark.asyncio
    async def test_no_block_when_charges_are_paid(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """חיובים ששולמו → לא חוסם גם אם בשני מחזורים"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        await station_wallet_factory(station_id=station.id)
        dispatcher = await user_factory(
            phone_number="+972500000002", name="סדרן",
        )
        courier = await user_factory(
            phone_number="+972502222222", name="נהג",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        service = StationService(db_session)
        current_cycle = service.get_billing_cycle_start()
        previous_cycle = service._get_previous_billing_cycle_start()

        # חיובים בשני מחזורים — אבל שולמו
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג",
            amount=100.0,
            courier_id=courier.id,
            is_paid=True,
            created_at=previous_cycle + timedelta(days=1),
        )
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג",
            amount=150.0,
            courier_id=courier.id,
            is_paid=True,
            created_at=current_cycle + timedelta(days=1),
        )

        blocked = await service.auto_block_unpaid_drivers(station.id)
        assert len(blocked) == 0

    @pytest.mark.asyncio
    async def test_no_block_when_already_blacklisted(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """נהג שכבר ברשימה שחורה → לא יוצר כפילות"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        await station_wallet_factory(station_id=station.id)
        dispatcher = await user_factory(
            phone_number="+972500000002", name="סדרן",
        )
        courier = await user_factory(
            phone_number="+972502222222", name="נהג",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        # הוספה ידנית לרשימה שחורה
        blacklist_entry = StationBlacklist(
            station_id=station.id,
            courier_id=courier.id,
            reason="חסימה ידנית",
        )
        db_session.add(blacklist_entry)
        await db_session.commit()

        service = StationService(db_session)
        current_cycle = service.get_billing_cycle_start()
        previous_cycle = service._get_previous_billing_cycle_start()

        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג",
            amount=100.0,
            courier_id=courier.id,
            created_at=previous_cycle + timedelta(days=1),
        )
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג",
            amount=150.0,
            courier_id=courier.id,
            created_at=current_cycle + timedelta(days=1),
        )

        blocked = await service.auto_block_unpaid_drivers(station.id)
        assert len(blocked) == 0  # כבר חסום - לא חוסם שוב

    @pytest.mark.asyncio
    async def test_no_block_without_courier_id(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """חיובים ללא courier_id → נדלגים בבטחה"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        await station_wallet_factory(station_id=station.id)
        dispatcher = await user_factory(
            phone_number="+972500000002", name="סדרן",
        )

        service = StationService(db_session)
        current_cycle = service.get_billing_cycle_start()
        previous_cycle = service._get_previous_billing_cycle_start()

        # חיובים ללא courier_id (ישנים לפני שלב 5)
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג אלמוני",
            amount=100.0,
            courier_id=None,
            created_at=previous_cycle + timedelta(days=1),
        )
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג אלמוני",
            amount=150.0,
            courier_id=None,
            created_at=current_cycle + timedelta(days=1),
        )

        blocked = await service.auto_block_unpaid_drivers(station.id)
        assert len(blocked) == 0

    @pytest.mark.asyncio
    async def test_auto_block_station_specific(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """חסימה ברמת תחנה — נהג חסום בתחנה אחת אבל לא בשנייה"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station_a = await station_factory(name="תחנה א", owner_id=owner.id)
        station_b = await station_factory(name="תחנה ב", owner_id=owner.id)
        await station_wallet_factory(station_id=station_a.id)
        await station_wallet_factory(station_id=station_b.id)
        dispatcher = await user_factory(
            phone_number="+972500000002", name="סדרן",
        )
        courier = await user_factory(
            phone_number="+972502222222", name="נהג",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        service = StationService(db_session)
        current_cycle = service.get_billing_cycle_start()
        previous_cycle = service._get_previous_billing_cycle_start()

        # חיובים ב-2 מחזורים בתחנה א בלבד
        await manual_charge_factory(
            station_id=station_a.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג",
            amount=100.0,
            courier_id=courier.id,
            created_at=previous_cycle + timedelta(days=1),
        )
        await manual_charge_factory(
            station_id=station_a.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג",
            amount=150.0,
            courier_id=courier.id,
            created_at=current_cycle + timedelta(days=1),
        )

        # רק תחנה א חוסמת
        blocked_a = await service.auto_block_unpaid_drivers(station_a.id)
        blocked_b = await service.auto_block_unpaid_drivers(station_b.id)

        assert len(blocked_a) == 1
        assert len(blocked_b) == 0
        assert await service.is_blacklisted(station_a.id, courier.id) is True
        assert await service.is_blacklisted(station_b.id, courier.id) is False


# ============================================================================
# TestBillingCycle - חישוב מחזורי חיוב
# ============================================================================

class TestBillingCycle:
    """בדיקות חישוב מחזורי חיוב (28 לחודש)"""

    def test_billing_cycle_start_after_28th(self):
        """אחרי ה-28 — מחזור התחיל ב-28 בחודש הנוכחי"""
        with patch("app.domain.services.station_service.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2025, 3, 29, 12, 0, 0)
            result = StationService.get_billing_cycle_start()
            assert result == datetime(2025, 3, 28, 0, 0, 0)

    def test_billing_cycle_start_before_28th(self):
        """לפני ה-28 — מחזור התחיל ב-28 בחודש הקודם"""
        with patch("app.domain.services.station_service.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2025, 3, 15, 12, 0, 0)
            result = StationService.get_billing_cycle_start()
            assert result == datetime(2025, 2, 28, 0, 0, 0)

    def test_billing_cycle_january_wraps_to_december(self):
        """ינואר לפני 28 — מחזור התחיל בדצמבר של שנה קודמת"""
        with patch("app.domain.services.station_service.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2025, 1, 15, 12, 0, 0)
            result = StationService.get_billing_cycle_start()
            assert result == datetime(2024, 12, 28, 0, 0, 0)

    def test_previous_billing_cycle_start(self):
        """מחזור קודם — חודש לפני המחזור הנוכחי"""
        with patch("app.domain.services.station_service.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2025, 3, 29, 12, 0, 0)
            result = StationService._get_previous_billing_cycle_start()
            # מחזור נוכחי: 28 מרץ, מחזור קודם: 28 פברואר
            assert result == datetime(2025, 2, 28, 0, 0, 0)

    def test_previous_billing_cycle_january_edge_case(self):
        """מחזור קודם כשנוכחי הוא ינואר — עוטף לדצמבר"""
        with patch("app.domain.services.station_service.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2025, 1, 29, 12, 0, 0)
            result = StationService._get_previous_billing_cycle_start()
            # מחזור נוכחי: 28 ינואר, מחזור קודם: 28 דצמבר
            assert result == datetime(2024, 12, 28, 0, 0, 0)


# ============================================================================
# TestCourierIdResolution - זיהוי אוטומטי של שליח
# ============================================================================

class TestCourierIdResolution:
    """בדיקות זיהוי אוטומטי של courier_id בחיוב ידני"""

    @pytest.mark.asyncio
    async def test_resolve_courier_id_exact_match(
        self, db_session, user_factory
    ):
        """שם מדויק של שליח → מזהה courier_id"""
        courier = await user_factory(
            phone_number="+972502222222", name="ישראל ישראלי",
            full_name="ישראל ישראלי",
            role=UserRole.COURIER,
        )

        service = StationService(db_session)
        result = await service._resolve_courier_id_by_name("ישראל ישראלי")
        assert result == courier.id

    @pytest.mark.asyncio
    async def test_resolve_courier_id_no_match(
        self, db_session, user_factory
    ):
        """שם שלא קיים → None"""
        await user_factory(
            phone_number="+972502222222", name="ישראל",
            role=UserRole.COURIER,
        )

        service = StationService(db_session)
        result = await service._resolve_courier_id_by_name("שם שלא קיים")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_courier_id_multiple_matches(
        self, db_session, user_factory
    ):
        """כמה שליחים עם אותו שם → None (אי אפשר לזהות)"""
        await user_factory(
            phone_number="+972502222222", name="ישראל",
            full_name="ישראל",
            role=UserRole.COURIER,
        )
        await user_factory(
            phone_number="+972502222223", name="ישראל",
            full_name="ישראל",
            role=UserRole.COURIER,
        )

        service = StationService(db_session)
        result = await service._resolve_courier_id_by_name("ישראל")
        assert result is None


# ============================================================================
# TestCreditStationCommission - עמלת תחנה ישירה
# ============================================================================

class TestCreditStationCommission:
    """בדיקות זיכוי עמלת תחנה ישירות"""

    @pytest.mark.asyncio
    async def test_commission_with_auto_commit_false(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, delivery_factory
    ):
        """זיכוי עמלה עם auto_commit=False — לא מבצע commit"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        await station_wallet_factory(station_id=station.id, balance=50.0)
        sender = await user_factory(
            phone_number="+972501111111", name="שולח"
        )
        delivery = await delivery_factory(sender_id=sender.id, fee=200.0)

        service = StationService(db_session)
        await service.credit_station_commission(
            station_id=station.id,
            delivery_id=delivery.id,
            fee=200.0,
            auto_commit=False,
        )

        # flush בוצע, אפשר לקרוא מה-session
        wallet = await service.get_station_wallet(station.id)
        assert wallet.balance == pytest.approx(70.0)  # 50 + 20 (10% של 200)

    @pytest.mark.asyncio
    async def test_commission_rejects_zero_fee(
        self, db_session, user_factory, station_factory,
        station_wallet_factory
    ):
        """עמלה 0 → ValidationException"""
        from app.core.exceptions import ValidationException

        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        await station_wallet_factory(station_id=station.id)

        service = StationService(db_session)
        with pytest.raises(ValidationException):
            await service.credit_station_commission(
                station_id=station.id,
                delivery_id=1,
                fee=0,
            )
