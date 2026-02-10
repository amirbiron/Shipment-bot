"""
בדיקות דשבורד פאנל
"""
import pytest
from app.core.auth import create_access_token
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.delivery import Delivery, DeliveryStatus


class TestPanelDashboard:
    """בדיקות דשבורד"""

    async def _setup_station(self, user_factory, db_session):
        """הכנת תחנה לבדיקות"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id, balance=500.0)
        db_session.add(wallet)
        await db_session.commit()
        return user, station

    def _get_token(self, user_id: int, station_id: int) -> str:
        """יצירת token לבדיקות"""
        return create_access_token(user_id, station_id, "station_owner")

    @pytest.mark.asyncio
    async def test_dashboard_returns_data(
        self, test_client, user_factory, db_session,
    ):
        """דשבורד מחזיר נתונים תקינים"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        response = await test_client.get(
            "/api/panel/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["station_name"] == "תחנת בדיקה"
        assert data["wallet_balance"] == 500.0
        assert data["active_deliveries_count"] == 0
        assert "today_deliveries_count" in data

    @pytest.mark.asyncio
    async def test_dashboard_counts_active_deliveries(
        self, test_client, user_factory, db_session,
    ):
        """דשבורד סופר משלוחים פעילים"""
        user, station = await self._setup_station(user_factory, db_session)

        # יצירת משלוחים
        sender = await user_factory(
            phone_number="+972509999999",
            name="שולח",
            role=UserRole.SENDER,
        )
        for s in [DeliveryStatus.OPEN, DeliveryStatus.CAPTURED, DeliveryStatus.DELIVERED]:
            d = Delivery(
                sender_id=sender.id,
                station_id=station.id,
                pickup_address="רחוב 1",
                dropoff_address="רחוב 2",
                status=s,
            )
            db_session.add(d)
        await db_session.commit()

        token = self._get_token(user.id, station.id)
        response = await test_client.get(
            "/api/panel/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = response.json()
        # OPEN + CAPTURED = 2, DELIVERED לא נחשב פעיל
        assert data["active_deliveries_count"] == 2

    @pytest.mark.asyncio
    async def test_dashboard_unauthorized(self, test_client):
        """גישה ללא token — 403"""
        response = await test_client.get("/api/panel/dashboard")
        assert response.status_code == 403
