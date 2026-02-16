"""
בדיקות דשבורד מולטי-תחנה — סעיף 9 מתוך issue #210
"""
import pytest

from app.core.auth import create_access_token
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_wallet import StationWallet
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_blacklist import StationBlacklist
from app.db.models.delivery import Delivery, DeliveryStatus


class TestMultiStationSetup:
    """הכנת נתוני בדיקה משותפים"""

    @staticmethod
    async def _setup_owner_with_stations(
        user_factory, db_session, station_count: int = 2,
    ) -> tuple:
        """יצירת בעל תחנה עם מספר תחנות"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנות",
            role=UserRole.STATION_OWNER,
        )

        stations = []
        for i in range(station_count):
            station = Station(name=f"תחנה {i + 1}", owner_id=user.id)
            db_session.add(station)
            await db_session.flush()

            # רשומת בעלים בטבלת junction
            owner_record = StationOwner(station_id=station.id, user_id=user.id)
            db_session.add(owner_record)

            wallet = StationWallet(
                station_id=station.id,
                balance=100.0 * (i + 1),
            )
            db_session.add(wallet)
            stations.append(station)

        await db_session.commit()
        return user, stations

    @staticmethod
    def _get_token(user_id: int, station_id: int) -> str:
        """יצירת token לבדיקות"""
        return create_access_token(user_id, station_id, "station_owner")


class TestStationsList(TestMultiStationSetup):
    """בדיקות רשימת תחנות"""

    @pytest.mark.asyncio
    async def test_list_stations_single_station(
        self, test_client, user_factory, db_session,
    ):
        """בעלים עם תחנה אחת — מקבל רשימה עם תחנה אחת"""
        user, stations = await self._setup_owner_with_stations(
            user_factory, db_session, station_count=1,
        )
        token = self._get_token(user.id, stations[0].id)

        response = await test_client.get(
            "/api/panel/stations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["stations"]) == 1
        assert data["current_station_id"] == stations[0].id
        assert data["stations"][0]["station_name"] == "תחנה 1"

    @pytest.mark.asyncio
    async def test_list_stations_multiple(
        self, test_client, user_factory, db_session,
    ):
        """בעלים עם שתי תחנות — מקבל רשימה עם שתיהן"""
        user, stations = await self._setup_owner_with_stations(
            user_factory, db_session, station_count=2,
        )
        token = self._get_token(user.id, stations[0].id)

        response = await test_client.get(
            "/api/panel/stations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["stations"]) == 2
        station_names = {s["station_name"] for s in data["stations"]}
        assert "תחנה 1" in station_names
        assert "תחנה 2" in station_names

    @pytest.mark.asyncio
    async def test_list_stations_totals(
        self, test_client, user_factory, db_session,
    ):
        """סכומים מצטברים נכונים"""
        user, stations = await self._setup_owner_with_stations(
            user_factory, db_session, station_count=2,
        )
        token = self._get_token(user.id, stations[0].id)

        response = await test_client.get(
            "/api/panel/stations",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = response.json()
        # ארנק: 100 + 200 = 300
        assert data["totals"]["total_wallet_balance"] == 300.0
        assert data["totals"]["total_active_deliveries"] == 0

    @pytest.mark.asyncio
    async def test_list_stations_with_deliveries(
        self, test_client, user_factory, db_session,
    ):
        """ספירת משלוחים פעילים נכונה לכל תחנה"""
        user, stations = await self._setup_owner_with_stations(
            user_factory, db_session, station_count=2,
        )

        # יצירת שולח
        sender = await user_factory(
            phone_number="+972509999999",
            name="שולח",
            role=UserRole.SENDER,
        )

        # 2 משלוחים פעילים לתחנה 1
        for s in [DeliveryStatus.OPEN, DeliveryStatus.CAPTURED]:
            d = Delivery(
                sender_id=sender.id,
                station_id=stations[0].id,
                pickup_address="רחוב 1",
                dropoff_address="רחוב 2",
                status=s,
            )
            db_session.add(d)

        # 1 משלוח פעיל + 1 מסור לתחנה 2
        d1 = Delivery(
            sender_id=sender.id,
            station_id=stations[1].id,
            pickup_address="רחוב 3",
            dropoff_address="רחוב 4",
            status=DeliveryStatus.IN_PROGRESS,
        )
        d2 = Delivery(
            sender_id=sender.id,
            station_id=stations[1].id,
            pickup_address="רחוב 5",
            dropoff_address="רחוב 6",
            status=DeliveryStatus.DELIVERED,
        )
        db_session.add_all([d1, d2])
        await db_session.commit()

        token = self._get_token(user.id, stations[0].id)
        response = await test_client.get(
            "/api/panel/stations",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = response.json()

        # מיון לפי station_id לבדיקה יציבה
        stations_data = sorted(data["stations"], key=lambda s: s["station_id"])
        assert stations_data[0]["active_deliveries_count"] == 2
        assert stations_data[1]["active_deliveries_count"] == 1

        # סכום מצטבר: 2 + 1 = 3
        assert data["totals"]["total_active_deliveries"] == 3

    @pytest.mark.asyncio
    async def test_list_stations_unauthorized(self, test_client):
        """גישה ללא token — 403"""
        response = await test_client.get("/api/panel/stations")
        assert response.status_code == 403


class TestMultiDashboard(TestMultiStationSetup):
    """בדיקות דשבורד מרובה-תחנות"""

    @pytest.mark.asyncio
    async def test_multi_dashboard_returns_all_stations(
        self, test_client, user_factory, db_session,
    ):
        """דשבורד מולטי מחזיר נתונים לכל התחנות"""
        user, stations = await self._setup_owner_with_stations(
            user_factory, db_session, station_count=3,
        )
        token = self._get_token(user.id, stations[0].id)

        response = await test_client.get(
            "/api/panel/stations/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["stations"]) == 3
        assert data["current_station_id"] == stations[0].id

    @pytest.mark.asyncio
    async def test_multi_dashboard_includes_dispatchers_and_blacklisted(
        self, test_client, user_factory, db_session,
    ):
        """דשבורד מולטי כולל סדרנים וחסומים"""
        user, stations = await self._setup_owner_with_stations(
            user_factory, db_session, station_count=2,
        )

        # הוספת סדרן לתחנה 1
        dispatcher = await user_factory(
            phone_number="+972508888888",
            name="סדרן",
            role=UserRole.SENDER,
        )
        sd = StationDispatcher(
            station_id=stations[0].id,
            user_id=dispatcher.id,
            is_active=True,
        )
        db_session.add(sd)

        # הוספת שליח חסום לתחנה 2
        courier = await user_factory(
            phone_number="+972507777777",
            name="שליח חסום",
            role=UserRole.COURIER,
        )
        bl = StationBlacklist(
            station_id=stations[1].id,
            courier_id=courier.id,
            reason="אי תשלום",
        )
        db_session.add(bl)
        await db_session.commit()

        token = self._get_token(user.id, stations[0].id)
        response = await test_client.get(
            "/api/panel/stations/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = response.json()
        stations_data = sorted(data["stations"], key=lambda s: s["station_id"])

        assert stations_data[0]["active_dispatchers_count"] == 1
        assert stations_data[1]["blacklisted_count"] == 1
        assert data["totals"]["total_active_dispatchers"] == 1
        assert data["totals"]["total_blacklisted"] == 1


class TestSwitchStation(TestMultiStationSetup):
    """בדיקות מעבר בין תחנות"""

    @pytest.mark.asyncio
    async def test_switch_station_success(
        self, test_client, user_factory, db_session,
    ):
        """מעבר מוצלח בין תחנות — מקבל טוקנים חדשים"""
        user, stations = await self._setup_owner_with_stations(
            user_factory, db_session, station_count=2,
        )
        token = self._get_token(user.id, stations[0].id)

        response = await test_client.post(
            "/api/panel/auth/switch-station",
            json={"station_id": stations[1].id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["station_id"] == stations[1].id
        assert data["station_name"] == "תחנה 2"
        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_switch_station_forbidden(
        self, test_client, user_factory, db_session,
    ):
        """מעבר לתחנה שאינה בבעלות — 403"""
        user, stations = await self._setup_owner_with_stations(
            user_factory, db_session, station_count=1,
        )

        # יצירת תחנה של בעלים אחר
        other_user = await user_factory(
            phone_number="+972506666666",
            name="בעלים אחר",
            role=UserRole.STATION_OWNER,
        )
        other_station = Station(name="תחנה אחרת", owner_id=other_user.id)
        db_session.add(other_station)
        await db_session.flush()
        other_owner = StationOwner(
            station_id=other_station.id, user_id=other_user.id,
        )
        db_session.add(other_owner)
        other_wallet = StationWallet(station_id=other_station.id)
        db_session.add(other_wallet)
        await db_session.commit()

        token = self._get_token(user.id, stations[0].id)
        response = await test_client.post(
            "/api/panel/auth/switch-station",
            json={"station_id": other_station.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_switch_station_new_token_works(
        self, test_client, user_factory, db_session,
    ):
        """הטוקן החדש שחוזר מ-switch עובד לקריאות הבאות"""
        user, stations = await self._setup_owner_with_stations(
            user_factory, db_session, station_count=2,
        )
        token = self._get_token(user.id, stations[0].id)

        # מעבר לתחנה 2
        switch_response = await test_client.post(
            "/api/panel/auth/switch-station",
            json={"station_id": stations[1].id},
            headers={"Authorization": f"Bearer {token}"},
        )
        new_token = switch_response.json()["access_token"]

        # שימוש בטוקן החדש — דשבורד של תחנה 2
        dashboard_response = await test_client.get(
            "/api/panel/dashboard",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert dashboard_response.status_code == 200
        assert dashboard_response.json()["station_name"] == "תחנה 2"

    @pytest.mark.asyncio
    async def test_switch_station_unauthorized(self, test_client):
        """מעבר ללא token — 403"""
        response = await test_client.post(
            "/api/panel/auth/switch-station",
            json={"station_id": 1},
        )
        assert response.status_code == 403
