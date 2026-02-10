"""
בדיקות משלוחים בפאנל
"""
import pytest
from app.core.auth import create_access_token
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.delivery import Delivery, DeliveryStatus


class TestPanelDeliveries:
    """בדיקות משלוחים"""

    async def _setup(self, user_factory, db_session, num_deliveries: int = 5):
        """הכנת תחנה עם משלוחים"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)

        sender = await user_factory(
            phone_number="+972509999999",
            name="שולח",
            role=UserRole.SENDER,
        )

        for i in range(num_deliveries):
            d = Delivery(
                sender_id=sender.id,
                station_id=station.id,
                pickup_address=f"רחוב {i}",
                dropoff_address=f"רחוב {i + 100}",
                status=DeliveryStatus.OPEN if i % 2 == 0 else DeliveryStatus.DELIVERED,
                fee=10.0,
            )
            db_session.add(d)
        await db_session.commit()
        return owner, station

    def _get_token(self, user_id: int, station_id: int) -> str:
        return create_access_token(user_id, station_id, "station_owner")

    @pytest.mark.asyncio
    async def test_active_deliveries_pagination(
        self, test_client, user_factory, db_session,
    ):
        """משלוחים פעילים עם pagination"""
        owner, station = await self._setup(user_factory, db_session, num_deliveries=10)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/deliveries/active?page=1&page_size=3",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 3
        assert data["page"] == 1
        assert data["page_size"] == 3
        assert data["total"] >= 0
        assert data["total_pages"] >= 1

    @pytest.mark.asyncio
    async def test_delivery_history(
        self, test_client, user_factory, db_session,
    ):
        """היסטוריית משלוחים"""
        owner, station = await self._setup(user_factory, db_session, num_deliveries=6)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/deliveries/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # רק משלוחים שהסתיימו (DELIVERED/CANCELLED)
        for item in data["items"]:
            assert item["status"] in ("delivered", "cancelled")

    @pytest.mark.asyncio
    async def test_delivery_history_date_filter(
        self, test_client, user_factory, db_session,
    ):
        """סינון היסטוריה לפי תאריך"""
        owner, station = await self._setup(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/deliveries/history?date_from=2020-01-01&date_to=2030-12-31",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_delivery_history_invalid_date(
        self, test_client, user_factory, db_session,
    ):
        """תאריך לא תקין — 400"""
        owner, station = await self._setup(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/deliveries/history?date_from=not-a-date",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_get_delivery_detail(
        self, test_client, user_factory, db_session,
    ):
        """פרטי משלוח בודד"""
        owner, station = await self._setup(user_factory, db_session, num_deliveries=1)
        token = self._get_token(owner.id, station.id)

        # קודם מקבלים את הרשימה כדי לקבל ID
        list_response = await test_client.get(
            "/api/panel/deliveries/active",
            headers={"Authorization": f"Bearer {token}"},
        )
        items = list_response.json()["items"]
        if items:
            delivery_id = items[0]["id"]
            response = await test_client.get(
                f"/api/panel/deliveries/{delivery_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            assert response.json()["id"] == delivery_id

    @pytest.mark.asyncio
    async def test_delivery_detail_not_found(
        self, test_client, user_factory, db_session,
    ):
        """משלוח לא קיים — 404"""
        owner, station = await self._setup(user_factory, db_session, num_deliveries=0)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/deliveries/99999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
