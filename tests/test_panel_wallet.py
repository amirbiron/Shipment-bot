"""
בדיקות ארנק תחנה בפאנל
"""
import pytest
from app.core.auth import create_access_token
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType


class TestPanelWallet:
    """בדיקות ארנק"""

    async def _setup(self, user_factory, db_session, with_ledger: bool = False):
        """הכנת תחנה עם ארנק"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id, balance=1500.0)
        db_session.add(wallet)

        if with_ledger:
            for i, et in enumerate([
                StationLedgerEntryType.COMMISSION_CREDIT,
                StationLedgerEntryType.MANUAL_CHARGE,
                StationLedgerEntryType.COMMISSION_CREDIT,
            ]):
                entry = StationLedger(
                    station_id=station.id,
                    entry_type=et,
                    amount=100.0 * (i + 1),
                    balance_after=100.0 * (i + 1),
                    description=f"תנועה {i + 1}",
                )
                db_session.add(entry)

        await db_session.commit()
        return owner, station

    def _get_token(self, user_id: int, station_id: int) -> str:
        return create_access_token(user_id, station_id, "station_owner")

    @pytest.mark.asyncio
    async def test_get_wallet(self, test_client, user_factory, db_session):
        """קבלת יתרת ארנק"""
        owner, station = await self._setup(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/wallet",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["balance"] == 1500.0
        assert data["commission_rate"] == 0.1

    @pytest.mark.asyncio
    async def test_get_ledger(self, test_client, user_factory, db_session):
        """היסטוריית תנועות"""
        owner, station = await self._setup(user_factory, db_session, with_ledger=True)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/wallet/ledger",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3
        assert "summary" in data

    @pytest.mark.asyncio
    async def test_ledger_filter_by_type(
        self, test_client, user_factory, db_session,
    ):
        """סינון תנועות לפי סוג"""
        owner, station = await self._setup(user_factory, db_session, with_ledger=True)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/wallet/ledger?entry_type=commission_credit",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # יש 2 תנועות מסוג COMMISSION_CREDIT
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_ledger_invalid_type(
        self, test_client, user_factory, db_session,
    ):
        """סוג תנועה לא תקין — 400"""
        owner, station = await self._setup(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/wallet/ledger?entry_type=invalid",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_ledger_pagination(
        self, test_client, user_factory, db_session,
    ):
        """pagination בתנועות"""
        owner, station = await self._setup(user_factory, db_session, with_ledger=True)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/wallet/ledger?page=1&page_size=2",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 3
        assert data["total_pages"] == 2
