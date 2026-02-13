"""
בדיקות endpoints תחנות עם הרשאת אדמין
"""
import pytest
from unittest.mock import patch

from app.core.config import settings
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.user import UserRole

_TEST_ADMIN_KEY = "test-admin-api-key-for-testing"


@pytest.fixture(autouse=True)
def set_admin_api_key():
    """מגדיר ADMIN_API_KEY לבדיקות"""
    with patch.object(settings, "ADMIN_API_KEY", _TEST_ADMIN_KEY):
        yield


class TestStationCreationRequiresAdmin:
    """בדיקות שיצירת תחנה דורשת מפתח אדמין"""

    @pytest.mark.asyncio
    async def test_create_station_without_api_key_rejected(self, test_client):
        """יצירת תחנה ללא מפתח — 401"""
        response = await test_client.post("/api/stations/", json={
            "name": "תחנת בדיקה",
            "owner_phone": "0501234567",
        })
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_station_wrong_api_key_rejected(self, test_client):
        """יצירת תחנה עם מפתח שגוי — 403"""
        response = await test_client.post(
            "/api/stations/",
            json={"name": "תחנת בדיקה", "owner_phone": "0501234567"},
            headers={"X-Admin-API-Key": "wrong-key"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_create_station_with_admin_key_succeeds(self, test_client):
        """יצירת תחנה עם מפתח אדמין תקין — 200"""
        response = await test_client.post(
            "/api/stations/",
            json={"name": "תחנה חדשה", "owner_phone": "0501234567"},
            headers={"X-Admin-API-Key": _TEST_ADMIN_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "תחנה חדשה"
        assert data["is_active"] is True

    @pytest.mark.asyncio
    async def test_create_station_duplicate_owner_rejected(
        self, test_client, user_factory, db_session,
    ):
        """יצירת תחנה למשתמש שכבר יש לו — 400"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה קיימת", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        response = await test_client.post(
            "/api/stations/",
            json={"name": "תחנה נוספת", "owner_phone": "0501234567"},
            headers={"X-Admin-API-Key": _TEST_ADMIN_KEY},
        )
        assert response.status_code == 400


class TestListStations:
    """בדיקות endpoint רשימת תחנות"""

    @pytest.mark.asyncio
    async def test_list_stations_without_api_key_rejected(self, test_client):
        """רשימת תחנות ללא מפתח — 401"""
        response = await test_client.get("/api/stations/")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_stations_empty(self, test_client):
        """רשימת תחנות ריקה"""
        response = await test_client.get(
            "/api/stations/",
            headers={"X-Admin-API-Key": _TEST_ADMIN_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["stations"] == []

    @pytest.mark.asyncio
    async def test_list_stations_returns_active_stations(
        self, test_client, user_factory, db_session,
    ):
        """רשימת תחנות מחזירה תחנות פעילות"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת אמיר", owner_id=user.id, is_active=True)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        response = await test_client.get(
            "/api/stations/",
            headers={"X-Admin-API-Key": _TEST_ADMIN_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["stations"][0]["name"] == "תחנת אמיר"


class TestGetStation:
    """בדיקות endpoint קבלת תחנה לפי ID"""

    @pytest.mark.asyncio
    async def test_get_station_without_api_key_rejected(self, test_client):
        """קבלת תחנה ללא מפתח — 401"""
        response = await test_client.get("/api/stations/1")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_station_not_found(self, test_client):
        """תחנה לא קיימת — 404"""
        response = await test_client.get(
            "/api/stations/99999",
            headers={"X-Admin-API-Key": _TEST_ADMIN_KEY},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_station_success(
        self, test_client, user_factory, db_session,
    ):
        """קבלת תחנה קיימת — 200"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת מבחן", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        response = await test_client.get(
            f"/api/stations/{station.id}",
            headers={"X-Admin-API-Key": _TEST_ADMIN_KEY},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "תחנת מבחן"
