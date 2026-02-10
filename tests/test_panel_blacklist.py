"""
בדיקות רשימה שחורה בפאנל
"""
import pytest
from app.core.auth import create_access_token
from app.db.models.user import UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.station_blacklist import StationBlacklist


class TestPanelBlacklist:
    """בדיקות רשימה שחורה"""

    async def _setup(self, user_factory, db_session):
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
        await db_session.commit()
        return owner, station

    def _get_token(self, user_id: int, station_id: int) -> str:
        return create_access_token(user_id, station_id, "station_owner")

    @pytest.mark.asyncio
    async def test_list_blacklist_empty(
        self, test_client, user_factory, db_session,
    ):
        """רשימה שחורה ריקה"""
        owner, station = await self._setup(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/blacklist",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_add_to_blacklist(
        self, test_client, user_factory, db_session,
    ):
        """הוספת נהג לרשימה שחורה"""
        owner, station = await self._setup(user_factory, db_session)
        await user_factory(
            phone_number="+972503333333",
            name="נהג",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.post(
            "/api/panel/blacklist",
            json={"phone_number": "0503333333", "reason": "לא שילם"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_add_duplicate_to_blacklist(
        self, test_client, user_factory, db_session,
    ):
        """הוספה כפולה — נכשל"""
        owner, station = await self._setup(user_factory, db_session)
        courier = await user_factory(
            phone_number="+972503333333",
            name="נהג",
            role=UserRole.COURIER,
        )
        # הוספה ידנית
        bl = StationBlacklist(
            station_id=station.id, courier_id=courier.id, reason="סיבה",
        )
        db_session.add(bl)
        await db_session.commit()

        token = self._get_token(owner.id, station.id)
        response = await test_client.post(
            "/api/panel/blacklist",
            json={"phone_number": "0503333333", "reason": "שוב"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_remove_from_blacklist(
        self, test_client, user_factory, db_session,
    ):
        """הסרה מרשימה שחורה"""
        owner, station = await self._setup(user_factory, db_session)
        courier = await user_factory(
            phone_number="+972503333333",
            name="נהג",
            role=UserRole.COURIER,
        )
        bl = StationBlacklist(
            station_id=station.id, courier_id=courier.id, reason="סיבה",
        )
        db_session.add(bl)
        await db_session.commit()

        token = self._get_token(owner.id, station.id)
        response = await test_client.delete(
            f"/api/panel/blacklist/{courier.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_bulk_add_to_blacklist(
        self, test_client, user_factory, db_session,
    ):
        """הוספה מרובה"""
        owner, station = await self._setup(user_factory, db_session)
        await user_factory(
            phone_number="+972504444444",
            name="נהג 1",
            role=UserRole.COURIER,
        )
        await user_factory(
            phone_number="+972505555555",
            name="נהג 2",
            role=UserRole.COURIER,
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.post(
            "/api/panel/blacklist/bulk",
            json={"entries": [
                {"phone_number": "0504444444", "reason": "סיבה 1"},
                {"phone_number": "0505555555", "reason": "סיבה 2"},
            ]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["success_count"] == 2
