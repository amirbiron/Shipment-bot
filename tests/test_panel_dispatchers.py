"""
בדיקות ניהול סדרנים בפאנל
"""
import pytest
from app.core.auth import create_access_token
from app.db.models.user import UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.station_dispatcher import StationDispatcher


class TestPanelDispatchers:
    """בדיקות סדרנים"""

    async def _setup_station(self, user_factory, db_session):
        """הכנת תחנה"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()
        return user, station

    def _get_token(self, user_id: int, station_id: int) -> str:
        return create_access_token(user_id, station_id, "station_owner")

    @pytest.mark.asyncio
    async def test_list_dispatchers_empty(
        self, test_client, user_factory, db_session,
    ):
        """רשימת סדרנים ריקה"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        response = await test_client.get(
            "/api/panel/dispatchers",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_add_dispatcher(
        self, test_client, user_factory, db_session,
    ):
        """הוספת סדרן"""
        user, station = await self._setup_station(user_factory, db_session)
        # יצירת משתמש שיהפוך לסדרן
        await user_factory(
            phone_number="+972502222222",
            name="סדרן חדש",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        token = self._get_token(user.id, station.id)
        response = await test_client.post(
            "/api/panel/dispatchers",
            json={"phone_number": "0502222222"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_add_dispatcher_invalid_phone(
        self, test_client, user_factory, db_session,
    ):
        """הוספת סדרן עם טלפון לא תקין"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        response = await test_client.post(
            "/api/panel/dispatchers",
            json={"phone_number": "invalid"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_bulk_add_dispatchers(
        self, test_client, user_factory, db_session,
    ):
        """הוספה מרובה — תוצאה לכל מספר"""
        user, station = await self._setup_station(user_factory, db_session)
        await user_factory(
            phone_number="+972502222222",
            name="סדרן 1",
            role=UserRole.COURIER,
        )
        await user_factory(
            phone_number="+972503333333",
            name="סדרן 2",
            role=UserRole.COURIER,
        )

        token = self._get_token(user.id, station.id)
        response = await test_client.post(
            "/api/panel/dispatchers/bulk",
            json={"phone_numbers": ["0502222222", "0503333333", "invalid"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        # 2 הצליחו, 1 נכשל (invalid)
        assert data["success_count"] == 2

    @pytest.mark.asyncio
    async def test_remove_dispatcher(
        self, test_client, user_factory, db_session,
    ):
        """הסרת סדרן"""
        user, station = await self._setup_station(user_factory, db_session)
        dispatcher_user = await user_factory(
            phone_number="+972502222222",
            name="סדרן",
            role=UserRole.COURIER,
        )
        # הוספה ידנית של סדרן
        sd = StationDispatcher(
            station_id=station.id, user_id=dispatcher_user.id,
        )
        db_session.add(sd)
        await db_session.commit()

        token = self._get_token(user.id, station.id)
        response = await test_client.delete(
            f"/api/panel/dispatchers/{dispatcher_user.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
