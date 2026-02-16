"""
בדיקות הגדרות תחנה מורחבות — סעיף 8 מתוך Issue #210

בדיקות שכבת השירות (StationService):
- עדכון שם, תיאור, שעות פעילות, אזורי שירות, לוגו
- ולידציה של קלט (שעות פעילות, אזורי שירות)
- מחיקת הגדרות (איפוס ל-null)

בדיקות ולידטורים:
- OperatingHoursValidator
- ServiceAreasValidator
"""
import pytest

from app.core.validation import OperatingHoursValidator, ServiceAreasValidator
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.domain.services.station_service import StationService


# ============================================================================
# בדיקות ולידטורים
# ============================================================================


class TestOperatingHoursValidator:
    """בדיקות ולידציית שעות פעילות"""

    @pytest.mark.unit
    def test_valid_hours(self):
        """שעות תקינות — עוברות ולידציה"""
        hours = {
            "sunday": {"open": "08:00", "close": "20:00"},
            "monday": {"open": "09:00", "close": "18:00"},
            "saturday": None,  # סגור
        }
        is_valid, error = OperatingHoursValidator.validate(hours)
        assert is_valid is True
        assert error is None

    @pytest.mark.unit
    def test_invalid_day(self):
        """יום לא תקין — נכשל"""
        hours = {"funday": {"open": "08:00", "close": "20:00"}}
        is_valid, error = OperatingHoursValidator.validate(hours)
        assert is_valid is False
        assert "יום לא תקין" in error

    @pytest.mark.unit
    def test_invalid_time_format(self):
        """פורמט שעה לא תקין — נכשל"""
        hours = {"sunday": {"open": "8:00", "close": "20:00"}}
        is_valid, error = OperatingHoursValidator.validate(hours)
        assert is_valid is False
        assert "שעת פתיחה לא תקינה" in error

    @pytest.mark.unit
    def test_invalid_close_time(self):
        """שעת סגירה לא תקינה — נכשל"""
        hours = {"sunday": {"open": "08:00", "close": "25:00"}}
        is_valid, error = OperatingHoursValidator.validate(hours)
        assert is_valid is False
        assert "שעת סגירה לא תקינה" in error

    @pytest.mark.unit
    def test_missing_open_close(self):
        """חסרים שדות open/close — נכשל"""
        hours = {"sunday": {"start": "08:00"}}
        is_valid, error = OperatingHoursValidator.validate(hours)
        assert is_valid is False
        assert "חסרים שדות" in error

    @pytest.mark.unit
    def test_not_dict(self):
        """קלט שאינו dict — נכשל"""
        is_valid, error = OperatingHoursValidator.validate("not a dict")
        assert is_valid is False

    @pytest.mark.unit
    def test_empty_dict(self):
        """dict ריק — תקין (אין ימים מוגדרים)"""
        is_valid, error = OperatingHoursValidator.validate({})
        assert is_valid is True

    @pytest.mark.unit
    def test_all_days_closed(self):
        """כל הימים סגורים — תקין"""
        hours = {day: None for day in OperatingHoursValidator.VALID_DAYS}
        is_valid, error = OperatingHoursValidator.validate(hours)
        assert is_valid is True


class TestServiceAreasValidator:
    """בדיקות ולידציית אזורי שירות"""

    @pytest.mark.unit
    def test_valid_areas(self):
        """אזורים תקינים — עוברות ולידציה"""
        areas = ["תל אביב", "רמת גן", "גבעתיים"]
        is_valid, error = ServiceAreasValidator.validate(areas)
        assert is_valid is True
        assert error is None

    @pytest.mark.unit
    def test_empty_area(self):
        """אזור ריק — נכשל"""
        areas = ["תל אביב", ""]
        is_valid, error = ServiceAreasValidator.validate(areas)
        assert is_valid is False
        assert "ריק" in error

    @pytest.mark.unit
    def test_too_many_areas(self):
        """יותר מדי אזורים — נכשל"""
        areas = [f"אזור {i}" for i in range(51)]
        is_valid, error = ServiceAreasValidator.validate(areas)
        assert is_valid is False
        assert "מקסימום" in error

    @pytest.mark.unit
    def test_not_list(self):
        """קלט שאינו רשימה — נכשל"""
        is_valid, error = ServiceAreasValidator.validate("not a list")
        assert is_valid is False

    @pytest.mark.unit
    def test_non_string_area(self):
        """אזור שאינו מחרוזת — נכשל"""
        areas = ["תל אביב", 123]
        is_valid, error = ServiceAreasValidator.validate(areas)
        assert is_valid is False
        assert "מחרוזת" in error

    @pytest.mark.unit
    def test_sanitize(self):
        """סניטציה — מסירה רווחים מיותרים ורשומות ריקות"""
        areas = ["  תל אביב  ", "רמת גן", "  ", ""]
        result = ServiceAreasValidator.sanitize(areas)
        assert result == ["תל אביב", "רמת גן"]


# ============================================================================
# בדיקות שכבת השירות
# ============================================================================


class TestStationSettingsService:
    """בדיקות עדכון הגדרות תחנה — StationService"""

    async def _create_station(self, user_factory, db_session):
        """יצירת תחנה לבדיקה"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()
        return station, user

    @pytest.mark.asyncio
    async def test_get_station_settings(self, user_factory, db_session):
        """קבלת הגדרות תחנה — ברירת מחדל"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        settings = await service.get_station_settings(station.id)

        assert settings["name"] == "תחנת בדיקה"
        assert settings["description"] is None
        assert settings["operating_hours"] is None
        assert settings["service_areas"] is None
        assert settings["logo_url"] is None

    @pytest.mark.asyncio
    async def test_update_name(self, user_factory, db_session):
        """עדכון שם תחנה"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        success, msg = await service.update_station_settings(
            station_id=station.id,
            name="שם חדש",
        )

        assert success is True
        settings = await service.get_station_settings(station.id)
        assert settings["name"] == "שם חדש"

    @pytest.mark.asyncio
    async def test_update_name_too_short(self, user_factory, db_session):
        """שם קצר מדי — נכשל"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        success, msg = await service.update_station_settings(
            station_id=station.id,
            name="א",
        )

        assert success is False
        assert "לפחות 2 תווים" in msg

    @pytest.mark.asyncio
    async def test_update_description(self, user_factory, db_session):
        """עדכון תיאור תחנה"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        success, msg = await service.update_station_settings(
            station_id=station.id,
            description="תחנת משלוחים מרכזית בתל אביב",
        )

        assert success is True
        settings = await service.get_station_settings(station.id)
        assert settings["description"] == "תחנת משלוחים מרכזית בתל אביב"

    @pytest.mark.asyncio
    async def test_clear_description(self, user_factory, db_session):
        """מחיקת תיאור — איפוס ל-null"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        # תחילה — הגדרת תיאור
        await service.update_station_settings(
            station_id=station.id,
            description="תיאור ישן",
        )

        # מחיקה
        success, msg = await service.update_station_settings(
            station_id=station.id,
            description=None,
        )

        assert success is True
        settings = await service.get_station_settings(station.id)
        assert settings["description"] is None

    @pytest.mark.asyncio
    async def test_update_operating_hours(self, user_factory, db_session):
        """עדכון שעות פעילות"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        hours = {
            "sunday": {"open": "08:00", "close": "20:00"},
            "saturday": None,
        }
        success, msg = await service.update_station_settings(
            station_id=station.id,
            operating_hours=hours,
        )

        assert success is True
        settings = await service.get_station_settings(station.id)
        assert settings["operating_hours"]["sunday"]["open"] == "08:00"
        assert settings["operating_hours"]["saturday"] is None

    @pytest.mark.asyncio
    async def test_update_operating_hours_invalid(self, user_factory, db_session):
        """שעות פעילות לא תקינות — נכשל"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        hours = {"funday": {"open": "08:00", "close": "20:00"}}
        success, msg = await service.update_station_settings(
            station_id=station.id,
            operating_hours=hours,
        )

        assert success is False
        assert "יום לא תקין" in msg

    @pytest.mark.asyncio
    async def test_update_service_areas(self, user_factory, db_session):
        """עדכון אזורי שירות"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        areas = ["תל אביב", "רמת גן", "גבעתיים"]
        success, msg = await service.update_station_settings(
            station_id=station.id,
            service_areas=areas,
        )

        assert success is True
        settings = await service.get_station_settings(station.id)
        assert settings["service_areas"] == ["תל אביב", "רמת גן", "גבעתיים"]

    @pytest.mark.asyncio
    async def test_clear_service_areas(self, user_factory, db_session):
        """מחיקת אזורי שירות"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        # הגדרה
        await service.update_station_settings(
            station_id=station.id,
            service_areas=["תל אביב"],
        )

        # מחיקה
        success, msg = await service.update_station_settings(
            station_id=station.id,
            service_areas=None,
        )

        assert success is True
        settings = await service.get_station_settings(station.id)
        assert settings["service_areas"] is None

    @pytest.mark.asyncio
    async def test_update_logo_url(self, user_factory, db_session):
        """עדכון לוגו"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        success, msg = await service.update_station_settings(
            station_id=station.id,
            logo_url="https://example.com/logo.png",
        )

        assert success is True
        settings = await service.get_station_settings(station.id)
        assert settings["logo_url"] == "https://example.com/logo.png"

    @pytest.mark.asyncio
    async def test_update_nonexistent_station(self, db_session):
        """עדכון תחנה שלא קיימת — נכשל"""
        service = StationService(db_session)

        success, msg = await service.update_station_settings(
            station_id=99999,
            name="שם חדש",
        )

        assert success is False
        assert "לא נמצאה" in msg

    @pytest.mark.asyncio
    async def test_partial_update(self, user_factory, db_session):
        """עדכון חלקי — רק שם, שאר ההגדרות לא משתנות"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        # הגדרת תיאור ואזורים
        await service.update_station_settings(
            station_id=station.id,
            description="תיאור",
            service_areas=["תל אביב"],
        )

        # עדכון רק שם — שאר ההגדרות (...) לא מתעדכנות
        success, msg = await service.update_station_settings(
            station_id=station.id,
            name="שם מעודכן",
        )

        assert success is True
        settings = await service.get_station_settings(station.id)
        assert settings["name"] == "שם מעודכן"
        assert settings["description"] == "תיאור"
        assert settings["service_areas"] == ["תל אביב"]

    @pytest.mark.asyncio
    async def test_update_all_settings(self, user_factory, db_session):
        """עדכון כל ההגדרות יחד"""
        station, _ = await self._create_station(user_factory, db_session)
        service = StationService(db_session)

        hours = {
            "sunday": {"open": "07:00", "close": "23:00"},
            "friday": {"open": "07:00", "close": "14:00"},
            "saturday": None,
        }

        success, msg = await service.update_station_settings(
            station_id=station.id,
            name="תחנה מרכזית",
            description="תחנה גדולה בתל אביב",
            operating_hours=hours,
            service_areas=["תל אביב", "יפו"],
            logo_url="https://example.com/logo.png",
        )

        assert success is True
        settings = await service.get_station_settings(station.id)
        assert settings["name"] == "תחנה מרכזית"
        assert settings["description"] == "תחנה גדולה בתל אביב"
        assert settings["operating_hours"]["sunday"]["open"] == "07:00"
        assert settings["operating_hours"]["saturday"] is None
        assert "תל אביב" in settings["service_areas"]
        assert settings["logo_url"] == "https://example.com/logo.png"
