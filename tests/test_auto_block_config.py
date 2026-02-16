"""
בדיקות סעיף 10 - חסימה אוטומטית מותאמת (Issue #210)

מכסה:
- הגדרות חסימה אוטומטית per-station (enabled, grace_months, min_debt)
- כיבוד הגדרות בזמן ריצת החסימה
- עדכון הגדרות דרך service layer
- API endpoints לצפייה ועדכון הגדרות חסימה אוטומטית
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.station_blacklist import StationBlacklist
from app.db.models.manual_charge import ManualCharge
from app.domain.services.station_service import StationService


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def station_factory(db_session):
    """יצירת תחנת בדיקה עם הגדרות חסימה אוטומטית"""
    async def _create(
        name: str = "תחנת בדיקה",
        owner_id: int = 1,
        auto_block_enabled: bool = True,
        auto_block_grace_months: int = 2,
        auto_block_min_debt: float = 0.0,
    ) -> Station:
        station = Station(
            name=name,
            owner_id=owner_id,
            auto_block_enabled=auto_block_enabled,
            auto_block_grace_months=auto_block_grace_months,
            auto_block_min_debt=auto_block_min_debt,
        )
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
# TestAutoBlockDisabled - חסימה מושבתת
# ============================================================================

class TestAutoBlockDisabled:
    """בדיקות שחסימה אוטומטית מכובה בפועל כשהיא מושבתת"""

    @pytest.mark.asyncio
    async def test_auto_block_disabled_skips_station(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """תחנה עם auto_block_enabled=False → לא חוסמת אף נהג"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            auto_block_enabled=False,
        )
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
        current_cycle = service.get_billing_cycle_start()
        previous_cycle = service._get_previous_billing_cycle_start()

        # חיובים בשני מחזורים — אבל חסימה מושבתת
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג חייב",
            amount=100.0,
            courier_id=courier.id,
            created_at=previous_cycle + timedelta(days=1),
        )
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג חייב",
            amount=150.0,
            courier_id=courier.id,
            created_at=current_cycle + timedelta(days=1),
        )

        blocked = await service.auto_block_unpaid_drivers(station.id)
        assert len(blocked) == 0
        assert await service.is_blacklisted(station.id, courier.id) is False


# ============================================================================
# TestAutoBlockGraceMonths - תקופת חסד מותאמת
# ============================================================================

class TestAutoBlockGraceMonths:
    """בדיקות תקופת חסד מותאמת"""

    @pytest.mark.asyncio
    async def test_grace_months_3_blocks_after_3_consecutive(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """תחנה עם grace_months=3 → חוסמת רק אחרי 3 מחזורים רצופים"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            auto_block_grace_months=3,
        )
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
        current_cycle = service.get_billing_cycle_start()
        previous_cycle = service._get_previous_billing_cycle_start(current_cycle)
        two_cycles_ago = service._get_previous_billing_cycle_start(previous_cycle)

        # חיובים ב-3 מחזורים רצופים
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג חייב",
            amount=100.0,
            courier_id=courier.id,
            created_at=two_cycles_ago + timedelta(days=1),
        )
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג חייב",
            amount=150.0,
            courier_id=courier.id,
            created_at=previous_cycle + timedelta(days=1),
        )
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג חייב",
            amount=200.0,
            courier_id=courier.id,
            created_at=current_cycle + timedelta(days=1),
        )

        blocked = await service.auto_block_unpaid_drivers(station.id)
        assert len(blocked) == 1
        assert blocked[0]["courier_id"] == courier.id

    @pytest.mark.asyncio
    async def test_grace_months_3_no_block_with_only_2_months(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """תחנה עם grace_months=3 → לא חוסמת עם רק 2 מחזורים"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            auto_block_grace_months=3,
        )
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

        # חיובים רק ב-2 מחזורים (לא מספיק ל-grace_months=3)
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
        assert len(blocked) == 0

    @pytest.mark.asyncio
    async def test_grace_months_1_blocks_with_single_month(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """תחנה עם grace_months=1 → חוסמת מיד אחרי מחזור אחד"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            auto_block_grace_months=1,
        )
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

        # חיוב במחזור הנוכחי בלבד
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג",
            amount=100.0,
            courier_id=courier.id,
            created_at=current_cycle + timedelta(days=1),
        )

        blocked = await service.auto_block_unpaid_drivers(station.id)
        assert len(blocked) == 1
        assert blocked[0]["courier_id"] == courier.id


# ============================================================================
# TestAutoBlockMinDebt - סף חוב מינימלי
# ============================================================================

class TestAutoBlockMinDebt:
    """בדיקות סף חוב מינימלי"""

    @pytest.mark.asyncio
    async def test_min_debt_filters_low_debt(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """נהג עם חוב נמוך מהסף → לא נחסם"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            auto_block_min_debt=500.0,
        )
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

        # חיובים ב-2 מחזורים — אבל סכום כולל 250 (נמוך מסף 500)
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
        assert len(blocked) == 0

    @pytest.mark.asyncio
    async def test_min_debt_blocks_high_debt(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """נהג עם חוב גבוה מהסף → נחסם"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            auto_block_min_debt=200.0,
        )
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

        # חיובים ב-2 מחזורים — סכום כולל 300 (מעל סף 200)
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג",
            amount=150.0,
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
        assert len(blocked) == 1


# ============================================================================
# TestUpdateAutoBlockSettings - עדכון הגדרות
# ============================================================================

class TestUpdateAutoBlockSettings:
    """בדיקות עדכון הגדרות חסימה אוטומטית"""

    @pytest.mark.asyncio
    async def test_update_all_settings(
        self, db_session, user_factory, station_factory
    ):
        """עדכון כל הגדרות החסימה האוטומטית"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        service = StationService(db_session)

        success, msg = await service.update_auto_block_settings(
            station_id=station.id,
            auto_block_enabled=False,
            auto_block_grace_months=4,
            auto_block_min_debt=100.0,
        )

        assert success is True
        # אימות שהערכים עודכנו
        updated = await service.get_station(station.id)
        assert updated.auto_block_enabled is False
        assert updated.auto_block_grace_months == 4
        assert updated.auto_block_min_debt == 100.0

    @pytest.mark.asyncio
    async def test_update_partial_settings(
        self, db_session, user_factory, station_factory
    ):
        """עדכון חלקי — רק שדות שנשלחו מתעדכנים"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            auto_block_grace_months=2,
            auto_block_min_debt=0.0,
        )
        service = StationService(db_session)

        success, msg = await service.update_auto_block_settings(
            station_id=station.id,
            auto_block_grace_months=5,
        )

        assert success is True
        updated = await service.get_station(station.id)
        assert updated.auto_block_grace_months == 5
        # שדות שלא עודכנו — נשארים כמו שהיו
        assert updated.auto_block_enabled is True
        assert updated.auto_block_min_debt == 0.0

    @pytest.mark.asyncio
    async def test_update_invalid_grace_months(
        self, db_session, user_factory, station_factory
    ):
        """תקופת חסד לא תקינה → שגיאה"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        service = StationService(db_session)

        success, msg = await service.update_auto_block_settings(
            station_id=station.id,
            auto_block_grace_months=0,
        )
        assert success is False

        success, msg = await service.update_auto_block_settings(
            station_id=station.id,
            auto_block_grace_months=13,
        )
        assert success is False

    @pytest.mark.asyncio
    async def test_update_negative_min_debt(
        self, db_session, user_factory, station_factory
    ):
        """סף חוב שלילי → שגיאה"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(owner_id=owner.id)
        service = StationService(db_session)

        success, msg = await service.update_auto_block_settings(
            station_id=station.id,
            auto_block_min_debt=-50.0,
        )
        assert success is False

    @pytest.mark.asyncio
    async def test_update_nonexistent_station(
        self, db_session
    ):
        """עדכון תחנה לא קיימת → שגיאה"""
        service = StationService(db_session)
        success, msg = await service.update_auto_block_settings(
            station_id=99999,
            auto_block_enabled=False,
        )
        assert success is False


# ============================================================================
# TestGetStationSettings - הגדרות תחנה כוללות חסימה אוטומטית
# ============================================================================

class TestGetStationSettingsWithAutoBlock:
    """בדיקות שהגדרות תחנה כוללות הגדרות חסימה אוטומטית"""

    @pytest.mark.asyncio
    async def test_settings_include_auto_block_fields(
        self, db_session, user_factory, station_factory
    ):
        """get_station_settings מחזיר גם הגדרות חסימה"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            auto_block_enabled=True,
            auto_block_grace_months=3,
            auto_block_min_debt=100.0,
        )
        service = StationService(db_session)

        settings = await service.get_station_settings(station.id)

        assert settings["auto_block_enabled"] is True
        assert settings["auto_block_grace_months"] == 3
        assert settings["auto_block_min_debt"] == 100.0


# ============================================================================
# TestAutoBlockDefaultBehavior - ברירת מחדל (תאימות אחורה)
# ============================================================================

class TestAutoBlockDefaultBehavior:
    """בדיקות שברירת המחדל שומרת על ההתנהגות הקיימת"""

    @pytest.mark.asyncio
    async def test_default_values_match_original_behavior(
        self, db_session, user_factory, station_factory,
        station_wallet_factory, manual_charge_factory
    ):
        """ברירות המחדל (enabled=True, grace=2, min_debt=0) → כמו ההתנהגות המקורית"""
        owner = await user_factory(
            phone_number="+972500000001", name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        # תחנה עם ברירות מחדל
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
        current_cycle = service.get_billing_cycle_start()
        previous_cycle = service._get_previous_billing_cycle_start()

        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג חייב",
            amount=100.0,
            courier_id=courier.id,
            created_at=previous_cycle + timedelta(days=1),
        )
        await manual_charge_factory(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג חייב",
            amount=150.0,
            courier_id=courier.id,
            created_at=current_cycle + timedelta(days=1),
        )

        blocked = await service.auto_block_unpaid_drivers(station.id)

        # אותה התנהגות כמו קודם — חוסם אחרי 2 מחזורים
        assert len(blocked) == 1
        assert blocked[0]["courier_id"] == courier.id
