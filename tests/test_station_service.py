"""
בדיקות יחידה ל-StationService — ניהול תחנות, בעלים, סדרנים וארנק תחנה
"""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_wallet import StationWallet
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType
from app.db.models.user import User, UserRole, ApprovalStatus
from app.domain.services.station_service import StationService


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
async def station_owner(user_factory):
    """יצירת בעל תחנה"""
    return await user_factory(
        phone_number="+972509999999",
        name="בעל תחנה",
        role=UserRole.STATION_OWNER,
    )


@pytest.fixture
async def station_with_owner(db_session, station_owner):
    """יצירת תחנה עם בעלים וארנק"""
    service = StationService(db_session)
    station = await service.create_station("תחנה לבדיקה", station_owner.id)
    await db_session.commit()
    return station


# ============================================================================
# בדיקות יצירת תחנה
# ============================================================================

@pytest.mark.unit
class TestCreateStation:

    async def test_create_station_basic(self, db_session, station_owner):
        """יצירת תחנה חדשה — צריכה ליצור תחנה, ארנק ורשומת בעלים"""
        service = StationService(db_session)
        station = await service.create_station("תחנת בדיקה", station_owner.id)
        await db_session.commit()

        assert station.id is not None
        assert station.name == "תחנת בדיקה"
        assert station.owner_id == station_owner.id

        # וידוא שנוצר ארנק
        result = await db_session.execute(
            select(StationWallet).where(StationWallet.station_id == station.id)
        )
        wallet = result.scalar_one()
        assert wallet.balance == Decimal("0.00")
        assert wallet.commission_rate == Decimal("0.10")

        # וידוא שנוצרה רשומת בעלים
        result = await db_session.execute(
            select(StationOwner).where(
                StationOwner.station_id == station.id,
                StationOwner.user_id == station_owner.id,
            )
        )
        owner_record = result.scalar_one()
        assert owner_record.is_active is True


# ============================================================================
# בדיקות שליפת תחנה
# ============================================================================

@pytest.mark.unit
class TestGetStation:

    async def test_get_station_by_id(self, db_session, station_with_owner):
        """שליפת תחנה פעילה לפי מזהה"""
        service = StationService(db_session)
        result = await service.get_station(station_with_owner.id)
        assert result is not None
        assert result.id == station_with_owner.id

    async def test_get_station_nonexistent(self, db_session):
        """תחנה לא קיימת — מחזיר None"""
        service = StationService(db_session)
        result = await service.get_station(99999)
        assert result is None

    async def test_get_station_by_owner(self, db_session, station_with_owner, station_owner):
        """שליפת תחנה לפי בעלים"""
        service = StationService(db_session)
        result = await service.get_station_by_owner(station_owner.id)
        assert result is not None
        assert result.id == station_with_owner.id

    async def test_is_owner_of_station(self, db_session, station_with_owner, station_owner):
        """בדיקת בעלות על תחנה"""
        service = StationService(db_session)
        assert await service.is_owner_of_station(station_owner.id, station_with_owner.id) is True

    async def test_is_not_owner(self, db_session, station_with_owner, user_factory):
        """משתמש שאינו בעלים"""
        other = await user_factory(phone_number="+972508888888", role=UserRole.SENDER)
        service = StationService(db_session)
        assert await service.is_owner_of_station(other.id, station_with_owner.id) is False


# ============================================================================
# בדיקות ניהול בעלים
# ============================================================================

@pytest.mark.unit
class TestManageOwners:

    async def test_add_owner(self, db_session, station_with_owner, station_owner):
        """הוספת בעלים חדש"""
        service = StationService(db_session)
        success, msg = await service.add_owner(
            station_with_owner.id,
            "0501234567",
            actor_user_id=station_owner.id,
        )

        assert success is True
        assert "נוסף" in msg

    async def test_add_owner_invalid_phone(self, db_session, station_with_owner, station_owner):
        """הוספת בעלים עם טלפון לא תקין — צריכה להיכשל"""
        service = StationService(db_session)
        success, msg = await service.add_owner(
            station_with_owner.id,
            "123",
            actor_user_id=station_owner.id,
        )

        assert success is False
        assert "לא תקין" in msg

    async def test_add_existing_owner(self, db_session, station_with_owner, station_owner):
        """הוספת בעלים שכבר קיים — צריכה להיכשל"""
        service = StationService(db_session)
        success, msg = await service.add_owner(
            station_with_owner.id,
            "+972509999999",  # מספר של station_owner
            actor_user_id=station_owner.id,
        )

        assert success is False
        assert "כבר בעלים" in msg

    async def test_remove_owner_prevents_last(
        self, db_session, station_with_owner, station_owner
    ):
        """הסרת הבעלים האחרון — צריכה להיכשל"""
        service = StationService(db_session)
        success, msg = await service.remove_owner(
            station_with_owner.id,
            station_owner.id,
            actor_user_id=station_owner.id,
        )

        assert success is False
        assert "האחרון" in msg

    async def test_remove_owner_with_multiple(
        self, db_session, station_with_owner, station_owner
    ):
        """הסרת בעלים כשיש יותר מאחד — צריכה להצליח"""
        service = StationService(db_session)
        # הוספת בעלים שני
        await service.add_owner(
            station_with_owner.id,
            "0507777777",
            actor_user_id=station_owner.id,
        )

        # הסרת הבעלים המקורי
        success, msg = await service.remove_owner(
            station_with_owner.id,
            station_owner.id,
            actor_user_id=station_owner.id,
        )

        assert success is True
        assert "הוסר" in msg


# ============================================================================
# בדיקות ניהול סדרנים
# ============================================================================

@pytest.mark.unit
class TestManageDispatchers:

    async def test_add_dispatcher(self, db_session, station_with_owner, station_owner):
        """הוספת סדרן"""
        service = StationService(db_session)
        success, msg = await service.add_dispatcher(
            station_with_owner.id,
            "0504444444",
            actor_user_id=station_owner.id,
        )

        assert success is True
        assert "נוסף" in msg

    async def test_add_dispatcher_invalid_phone(
        self, db_session, station_with_owner, station_owner
    ):
        """הוספת סדרן עם טלפון לא תקין"""
        service = StationService(db_session)
        success, msg = await service.add_dispatcher(
            station_with_owner.id,
            "abc",
            actor_user_id=station_owner.id,
        )

        assert success is False
        assert "לא תקין" in msg


# ============================================================================
# בדיקות ארנק תחנה ועמלות
# ============================================================================

@pytest.mark.unit
class TestStationWallet:

    async def test_get_station_wallet(self, db_session, station_with_owner):
        """שליפת ארנק תחנה"""
        service = StationService(db_session)
        wallet = await service.get_station_wallet(station_with_owner.id)

        assert wallet is not None
        assert wallet.balance == Decimal("0.00")
        assert wallet.commission_rate == Decimal("0.10")

    async def test_credit_station_commission(self, db_session, station_with_owner, user_factory):
        """זיכוי עמלת תחנה"""
        sender = await user_factory(phone_number="+972501111111", role=UserRole.SENDER)
        from app.db.models.delivery import Delivery, DeliveryStatus
        delivery = Delivery(
            sender_id=sender.id,
            pickup_address="א",
            dropoff_address="ב",
            status=DeliveryStatus.DELIVERED,
            fee=100.0,
        )
        db_session.add(delivery)
        await db_session.flush()

        service = StationService(db_session)
        await service.credit_station_commission(
            station_id=station_with_owner.id,
            delivery_id=delivery.id,
            fee=100.0,
            auto_commit=True,
        )

        # וידוא שהארנק עודכן (10% מ-100 = 10)
        wallet = await service.get_station_wallet(station_with_owner.id)
        assert wallet.balance == Decimal("10.00")

        # וידוא שנוצרה רשומת ledger
        result = await db_session.execute(
            select(StationLedger).where(
                StationLedger.station_id == station_with_owner.id,
                StationLedger.delivery_id == delivery.id,
            )
        )
        ledger = result.scalar_one()
        assert ledger.entry_type == StationLedgerEntryType.COMMISSION_CREDIT
        assert ledger.amount == Decimal("10.00")


# ============================================================================
# בדיקות עדכון אחוז עמלה
# ============================================================================

@pytest.mark.unit
class TestUpdateCommissionRate:

    async def test_update_valid_rate(self, db_session, station_with_owner, station_owner):
        """עדכון אחוז עמלה תקין — בטווח 6%-12%"""
        service = StationService(db_session)
        success, msg = await service.update_commission_rate(
            station_with_owner.id,
            0.08,
            actor_user_id=station_owner.id,
        )

        assert success is True

        wallet = await service.get_station_wallet(station_with_owner.id)
        assert wallet.commission_rate == Decimal("0.08")

    async def test_update_rate_too_low(self, db_session, station_with_owner, station_owner):
        """אחוז עמלה נמוך מדי — צריך להיכשל"""
        service = StationService(db_session)
        success, msg = await service.update_commission_rate(
            station_with_owner.id,
            0.01,
            actor_user_id=station_owner.id,
        )

        assert success is False

    async def test_update_rate_too_high(self, db_session, station_with_owner, station_owner):
        """אחוז עמלה גבוה מדי — צריך להיכשל"""
        service = StationService(db_session)
        success, msg = await service.update_commission_rate(
            station_with_owner.id,
            0.50,
            actor_user_id=station_owner.id,
        )

        assert success is False
