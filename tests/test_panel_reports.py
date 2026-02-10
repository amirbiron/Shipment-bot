"""
בדיקות דוחות בפאנל
"""
import pytest
from app.core.auth import create_access_token
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.manual_charge import ManualCharge
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType


class TestPanelReports:
    """בדיקות דוחות"""

    async def _setup(self, user_factory, db_session, with_data: bool = False):
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

        dispatcher = await user_factory(
            phone_number="+972509999999",
            name="סדרן",
            role=UserRole.COURIER,
        )

        if with_data:
            # חיובים ידניים
            for i, name in enumerate(["משה כהן", "משה כהן", "דני לוי"]):
                charge = ManualCharge(
                    station_id=station.id,
                    dispatcher_id=dispatcher.id,
                    driver_name=name,
                    amount=100.0 * (i + 1),
                    description=f"חיוב {i + 1}",
                )
                db_session.add(charge)

            # תנועות לדג'ר
            for et, amount in [
                (StationLedgerEntryType.COMMISSION_CREDIT, 50.0),
                (StationLedgerEntryType.MANUAL_CHARGE, 200.0),
                (StationLedgerEntryType.WITHDRAWAL, 30.0),
            ]:
                entry = StationLedger(
                    station_id=station.id,
                    entry_type=et,
                    amount=amount,
                    balance_after=amount,
                    description="דוח",
                )
                db_session.add(entry)

        await db_session.commit()
        return owner, station

    def _get_token(self, user_id: int, station_id: int) -> str:
        return create_access_token(user_id, station_id, "station_owner")

    @pytest.mark.asyncio
    async def test_collection_report(
        self, test_client, user_factory, db_session,
    ):
        """דוח גבייה מחזיר נתונים"""
        owner, station = await self._setup(user_factory, db_session, with_data=True)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/reports/collection",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) > 0
        assert data["total_debt"] > 0
        # "משה כהן" צריך להופיע עם 2 חיובים
        moshe = [i for i in data["items"] if i["driver_name"] == "משה כהן"]
        assert len(moshe) == 1
        assert moshe[0]["charge_count"] == 2

    @pytest.mark.asyncio
    async def test_collection_report_empty(
        self, test_client, user_factory, db_session,
    ):
        """דוח גבייה ריק"""
        owner, station = await self._setup(user_factory, db_session, with_data=False)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/reports/collection",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total_debt"] == 0

    @pytest.mark.asyncio
    async def test_export_csv(
        self, test_client, user_factory, db_session,
    ):
        """ייצוא CSV — headers תקינים"""
        owner, station = await self._setup(user_factory, db_session, with_data=True)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/reports/collection/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]
        # בודקים שהתוכן מכיל BOM + כותרות עבריות
        content = response.text
        assert "שם נהג" in content

    @pytest.mark.asyncio
    async def test_revenue_report(
        self, test_client, user_factory, db_session,
    ):
        """דוח הכנסות"""
        owner, station = await self._setup(user_factory, db_session, with_data=True)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/reports/revenue?date_from=2020-01-01&date_to=2030-12-31",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_commissions"] == 50.0
        assert data["total_manual_charges"] == 200.0
        assert data["total_withdrawals"] == 30.0
        assert data["net_total"] == 220.0  # 50 + 200 - 30

    @pytest.mark.asyncio
    async def test_revenue_report_default_dates(
        self, test_client, user_factory, db_session,
    ):
        """דוח הכנסות עם ברירת מחדל"""
        owner, station = await self._setup(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/reports/revenue",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
