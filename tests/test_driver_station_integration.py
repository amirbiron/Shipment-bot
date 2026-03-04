"""
בדיקות אינטגרציה — סשן 9: תפקידי תחנה וסדרן (iDriver)

בודק:
1. מודל DispatcherRide — יצירה, שליפה, סטטוסים
2. station_service — create_dispatcher_ride, get_open_rides_for_search
3. אינטגרציית חיפוש — נסיעות סדרן מופיעות בתוצאות חיפוש נהג
4. הרשאות — סדרן יכול לפרסם רק בתחנה שלו
"""
import pytest
from decimal import Decimal

from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_wallet import StationWallet
from app.db.models.dispatcher_ride import DispatcherRide, DispatcherRideStatus
from app.domain.services.station_service import StationService


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
async def station_with_dispatcher(db_session, user_factory):
    """תחנה פעילה עם סדרן משויך"""
    owner = await user_factory(
        phone_number="+972501110001",
        name="בעל תחנה",
        role=UserRole.STATION_OWNER,
    )
    dispatcher_user = await user_factory(
        phone_number="+972501110002",
        name="סדרן",
        role=UserRole.SENDER,
    )

    station = Station(
        name="תחנת בדיקה",
        owner_id=owner.id,
        is_active=True,
        service_areas=["תל אביב", "ירושלים"],
        public_group_chat_id="-100123456",
        public_group_platform="telegram",
    )
    db_session.add(station)
    await db_session.commit()
    await db_session.refresh(station)

    # ארנק תחנה
    wallet = StationWallet(station_id=station.id, balance=Decimal("0"))
    db_session.add(wallet)
    await db_session.commit()

    # שיוך סדרן
    sd = StationDispatcher(
        station_id=station.id,
        user_id=dispatcher_user.id,
        is_active=True,
    )
    db_session.add(sd)
    await db_session.commit()

    return station, dispatcher_user, owner


@pytest.fixture
async def driver_user(user_factory):
    """משתמש נהג רגיל"""
    return await user_factory(
        phone_number="+972501110003",
        name="נהג",
        role=UserRole.DRIVER,
    )


@pytest.fixture
async def driver_dispatcher(db_session, user_factory):
    """נהג שהוא גם סדרן — לבדיקת routing"""
    driver = await user_factory(
        phone_number="+972501110004",
        name="נהג-סדרן",
        role=UserRole.DRIVER,
    )
    owner = await user_factory(
        phone_number="+972501110005",
        name="בעל תחנה 2",
        role=UserRole.STATION_OWNER,
    )
    station = Station(
        name="תחנת נהג-סדרן",
        owner_id=owner.id,
        is_active=True,
    )
    db_session.add(station)
    await db_session.commit()
    await db_session.refresh(station)

    wallet = StationWallet(station_id=station.id, balance=Decimal("0"))
    db_session.add(wallet)

    sd = StationDispatcher(
        station_id=station.id,
        user_id=driver.id,
        is_active=True,
    )
    db_session.add(sd)
    await db_session.commit()

    return driver, station


# ============================================================================
# מודל DispatcherRide
# ============================================================================

class TestDispatcherRideModel:
    """בדיקות בסיסיות למודל DispatcherRide"""

    @pytest.mark.unit
    async def test_create_dispatcher_ride_model(
        self, db_session, station_with_dispatcher
    ):
        """יצירת רשומת נסיעה ישירות ב-DB"""
        station, dispatcher, _owner = station_with_dispatcher

        ride = DispatcherRide(
            dispatcher_id=dispatcher.id,
            station_id=station.id,
            origin_city="תל אביב",
            destination_city="ירושלים",
            seats=4,
            price=Decimal("150.00"),
            description="נסיעת בדיקה",
            is_delivery=False,
            status=DispatcherRideStatus.OPEN.value,
        )
        db_session.add(ride)
        await db_session.commit()
        await db_session.refresh(ride)

        assert ride.id is not None
        assert ride.origin_city == "תל אביב"
        assert ride.destination_city == "ירושלים"
        assert ride.seats == 4
        assert ride.price == Decimal("150.00")
        assert ride.status == DispatcherRideStatus.OPEN.value
        assert ride.taken_by_user_id is None

    @pytest.mark.unit
    async def test_dispatcher_ride_status_enum(self):
        """ולידציה של ערכי סטטוס"""
        assert DispatcherRideStatus.OPEN.value == "open"
        assert DispatcherRideStatus.TAKEN.value == "taken"
        assert DispatcherRideStatus.CANCELLED.value == "cancelled"
        assert DispatcherRideStatus.EXPIRED.value == "expired"


# ============================================================================
# StationService — יצירת נסיעה
# ============================================================================

class TestStationServiceDispatcherRide:
    """בדיקות ל-create_dispatcher_ride ו-get methods"""

    @pytest.mark.unit
    async def test_create_dispatcher_ride_success(
        self, db_session, station_with_dispatcher
    ):
        """יצירת נסיעה תקינה דרך station_service"""
        station, dispatcher, _owner = station_with_dispatcher
        service = StationService(db_session)

        ride = await service.create_dispatcher_ride(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            origin_city="תל אביב",
            destination_city="ירושלים",
            seats=3,
            price=Decimal("120.00"),
            description="נסיעה רגילה",
        )

        assert ride.id is not None
        assert ride.origin_city == "תל אביב"
        assert ride.destination_city == "ירושלים"
        assert ride.seats == 3
        assert ride.price == Decimal("120.00")
        assert ride.status == DispatcherRideStatus.OPEN.value
        assert ride.station_id == station.id
        assert ride.dispatcher_id == dispatcher.id

    @pytest.mark.unit
    async def test_create_dispatcher_ride_unauthorized(
        self, db_session, station_with_dispatcher, driver_user
    ):
        """סדרן לא משויך לתחנה — דחייה"""
        station, _dispatcher, _owner = station_with_dispatcher
        service = StationService(db_session)

        with pytest.raises(Exception) as exc_info:
            await service.create_dispatcher_ride(
                station_id=station.id,
                dispatcher_id=driver_user.id,
                origin_city="חיפה",
                destination_city="באר שבע",
                seats=2,
                price=Decimal("80.00"),
            )
        assert "הרשאה" in str(exc_info.value) or "סדרן" in str(exc_info.value)

    @pytest.mark.unit
    async def test_get_station_active_rides(
        self, db_session, station_with_dispatcher
    ):
        """שליפת נסיעות פעילות של תחנה"""
        station, dispatcher, _owner = station_with_dispatcher
        service = StationService(db_session)

        # יצירת 2 נסיעות פתוחות + 1 מבוטלת
        for city in ["ירושלים", "חיפה"]:
            await service.create_dispatcher_ride(
                station_id=station.id,
                dispatcher_id=dispatcher.id,
                origin_city="תל אביב",
                destination_city=city,
                seats=3,
                price=Decimal("100.00"),
            )
        cancelled_ride = DispatcherRide(
            dispatcher_id=dispatcher.id,
            station_id=station.id,
            origin_city="תל אביב",
            destination_city="אילת",
            seats=2,
            price=Decimal("200.00"),
            status=DispatcherRideStatus.CANCELLED.value,
        )
        db_session.add(cancelled_ride)
        await db_session.commit()

        active = await service.get_station_active_rides(station.id)
        assert len(active) == 2
        destinations = {r.destination_city for r in active}
        assert "ירושלים" in destinations
        assert "חיפה" in destinations
        # מבוטלת לא אמורה להיכלל
        assert "אילת" not in destinations

    @pytest.mark.unit
    async def test_get_open_rides_for_search_no_filter(
        self, db_session, station_with_dispatcher
    ):
        """שליפת כל הנסיעות הפתוחות — ללא סינון"""
        station, dispatcher, _owner = station_with_dispatcher
        service = StationService(db_session)

        await service.create_dispatcher_ride(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            origin_city="תל אביב",
            destination_city="ירושלים",
            seats=4,
            price=Decimal("100.00"),
        )

        rides = await service.get_open_rides_for_search()
        assert len(rides) >= 1
        assert rides[0].status == DispatcherRideStatus.OPEN.value

    @pytest.mark.unit
    async def test_get_open_rides_for_search_with_filter(
        self, db_session, station_with_dispatcher
    ):
        """שליפת נסיעות פתוחות עם סינון לפי עיר"""
        station, dispatcher, _owner = station_with_dispatcher
        service = StationService(db_session)

        await service.create_dispatcher_ride(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            origin_city="תל אביב",
            destination_city="ירושלים",
            seats=4,
            price=Decimal("100.00"),
        )
        await service.create_dispatcher_ride(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            origin_city="חיפה",
            destination_city="באר שבע",
            seats=2,
            price=Decimal("80.00"),
        )

        # סינון לפי יעד
        rides = await service.get_open_rides_for_search(
            destination_city="ירושלים"
        )
        assert len(rides) == 1
        assert rides[0].destination_city == "ירושלים"

        # סינון לפי מוצא
        rides = await service.get_open_rides_for_search(
            origin_city="חיפה"
        )
        assert len(rides) == 1
        assert rides[0].origin_city == "חיפה"

    @pytest.mark.unit
    async def test_open_rides_exclude_taken_and_cancelled(
        self, db_session, station_with_dispatcher
    ):
        """נסיעות שאינן OPEN לא מוחזרות בחיפוש"""
        station, dispatcher, _owner = station_with_dispatcher
        service = StationService(db_session)

        # נסיעה פתוחה
        await service.create_dispatcher_ride(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            origin_city="תל אביב",
            destination_city="ירושלים",
            seats=4,
            price=Decimal("100.00"),
        )
        # נסיעות שאינן פתוחות
        for status in [DispatcherRideStatus.TAKEN, DispatcherRideStatus.CANCELLED]:
            ride = DispatcherRide(
                dispatcher_id=dispatcher.id,
                station_id=station.id,
                origin_city="תל אביב",
                destination_city="ירושלים",
                seats=2,
                price=Decimal("50.00"),
                status=status.value,
            )
            db_session.add(ride)
        await db_session.commit()

        rides = await service.get_open_rides_for_search(destination_city="ירושלים")
        assert len(rides) == 1
        assert rides[0].status == DispatcherRideStatus.OPEN.value


# ============================================================================
# הרשאות סדרן
# ============================================================================

class TestDispatcherAuthorization:
    """בדיקות הרשאות — סדרן חייב להיות משויך לתחנה"""

    @pytest.mark.unit
    async def test_dispatcher_station_lookup(
        self, db_session, station_with_dispatcher
    ):
        """סדרן פעיל מזוהה כמשויך לתחנה"""
        station, dispatcher, _owner = station_with_dispatcher
        service = StationService(db_session)

        found_station = await service.get_dispatcher_station(dispatcher.id)
        assert found_station is not None
        assert found_station.id == station.id

    @pytest.mark.unit
    async def test_non_dispatcher_not_found(
        self, db_session, driver_user
    ):
        """משתמש שאינו סדרן — אין תחנה"""
        service = StationService(db_session)
        found_station = await service.get_dispatcher_station(driver_user.id)
        assert found_station is None

    @pytest.mark.unit
    async def test_driver_dispatcher_routing(
        self, db_session, driver_dispatcher
    ):
        """נהג שהוא גם סדרן — מזוהה דרך get_dispatcher_station"""
        driver, station = driver_dispatcher
        service = StationService(db_session)

        found_station = await service.get_dispatcher_station(driver.id)
        assert found_station is not None
        assert found_station.id == station.id
