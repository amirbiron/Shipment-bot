"""
בדיקות יחידה — iDriver סשן 1: שכבת נתונים בסיסית

בודק:
- מודלים (DriverProfile, DriverSearchSettings, DriverSearch, DriverSession)
- ערכי enum (VehicleCategory, DressCode, VerificationStatus וכו')
- ולידציית Pydantic (DriverProfileCreate, DriverSearchCreate)
- DriverState enum ומעברי מצבים
- UserRole.DRIVER
- מגבלות עסקיות (גיל, מקסימום חיפושים)
"""
import pytest
from datetime import date, datetime, time, timedelta

from sqlalchemy import select, func

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    VehicleCategory,
    DressCode,
    DriverVerificationStatus,
    DriverSubscriptionStatus,
)
from app.db.models.driver_search_settings import (
    DriverSearchSettings,
    TripTypeFilter,
    UpcomingTimeframe,
)
from app.db.models.driver_search import (
    DriverSearch,
    DriverSearchStatus,
    MAX_ACTIVE_SEARCHES_PER_USER,
)
from app.db.models.driver_session import DriverSession
from app.state_machine.states import DriverState, DRIVER_TRANSITIONS
from app.schemas.driver import (
    DriverProfileCreate,
    DriverSearchCreate,
    DriverSearchSettingsUpdate,
)


# ============================================================================
# בדיקות Enum
# ============================================================================


class TestEnums:
    """ולידציית ערכי enum — וודא שכל הערכים הצפויים קיימים"""

    @pytest.mark.unit
    def test_user_role_has_driver(self) -> None:
        """UserRole חייב לכלול DRIVER"""
        assert UserRole.DRIVER.value == "driver"
        assert UserRole.DRIVER in UserRole

    @pytest.mark.unit
    def test_vehicle_category_values(self) -> None:
        """VehicleCategory צריך לכלול 7 קטגוריות"""
        expected = {"car", "4_seater", "7_seater", "8_plus", "motorcycle", "truck", "van"}
        actual = {e.value for e in VehicleCategory}
        assert actual == expected

    @pytest.mark.unit
    def test_dress_code_values(self) -> None:
        """DressCode צריך לכלול 6 אפשרויות"""
        expected = {
            "hassidic", "ultra_orthodox", "modern_orthodox",
            "religious_elegant", "mixed", "secular",
        }
        actual = {e.value for e in DressCode}
        assert actual == expected

    @pytest.mark.unit
    def test_verification_status_values(self) -> None:
        """DriverVerificationStatus צריך לכלול 4 מצבים"""
        expected = {"unverified", "pending", "approved", "rejected"}
        actual = {e.value for e in DriverVerificationStatus}
        assert actual == expected

    @pytest.mark.unit
    def test_subscription_status_values(self) -> None:
        """DriverSubscriptionStatus צריך לכלול 5 מצבים"""
        expected = {"trial", "active", "expired", "paused", "cancelled"}
        actual = {e.value for e in DriverSubscriptionStatus}
        assert actual == expected

    @pytest.mark.unit
    def test_trip_type_filter_values(self) -> None:
        """TripTypeFilter צריך לכלול 5 אפשרויות"""
        expected = {"short_distance", "medium_distance", "long_distance", "any_distance", "rides"}
        actual = {e.value for e in TripTypeFilter}
        assert actual == expected

    @pytest.mark.unit
    def test_upcoming_timeframe_values(self) -> None:
        """UpcomingTimeframe צריך לכלול 4 אפשרויות"""
        expected = {"1_hour", "2_hours", "5_hours", "all"}
        actual = {e.value for e in UpcomingTimeframe}
        assert actual == expected

    @pytest.mark.unit
    def test_search_status_values(self) -> None:
        """DriverSearchStatus צריך לכלול 3 מצבים"""
        expected = {"active", "paused", "deleted"}
        actual = {e.value for e in DriverSearchStatus}
        assert actual == expected

    @pytest.mark.unit
    def test_max_active_searches_is_9(self) -> None:
        """מגבלת חיפושים פעילים למשתמש"""
        assert MAX_ACTIVE_SEARCHES_PER_USER == 9


# ============================================================================
# בדיקות DriverState
# ============================================================================


class TestDriverState:
    """בדיקות מכונת מצבים לנהג"""

    @pytest.mark.unit
    def test_all_states_exist(self) -> None:
        """DriverState צריך לכלול את כל המצבים הצפויים"""
        expected_prefixes = ["DRIVER.INITIAL", "DRIVER.NEW", "DRIVER.REGISTER.",
                             "DRIVER.VERIFY.", "DRIVER.MENU", "DRIVER.SETTINGS.",
                             "DRIVER.SEARCH.", "DRIVER.SUBSCRIPTION."]
        state_values = {s.value for s in DriverState}
        for prefix in expected_prefixes:
            matches = [v for v in state_values if v.startswith(prefix)]
            assert len(matches) > 0, f"אין מצבים עם prefix {prefix}"

    @pytest.mark.unit
    def test_transitions_cover_all_states(self) -> None:
        """כל מצב ב-DriverState חייב להופיע ב-DRIVER_TRANSITIONS כמקור או כיעד"""
        all_states_in_transitions = set()
        for source, targets in DRIVER_TRANSITIONS.items():
            all_states_in_transitions.add(source)
            all_states_in_transitions.update(targets)
        for state in DriverState:
            assert state in all_states_in_transitions, \
                f"מצב {state.value} לא מופיע ב-DRIVER_TRANSITIONS"

    @pytest.mark.unit
    def test_initial_transitions_to_register(self) -> None:
        """מצב INITIAL חייב לעבור לרישום שם"""
        targets = DRIVER_TRANSITIONS[DriverState.INITIAL]
        assert DriverState.REGISTER_COLLECT_NAME in targets

    @pytest.mark.unit
    def test_registration_flow_order(self) -> None:
        """זרימת רישום: שם → תאריך לידה → רכב → קוד לבוש"""
        assert DriverState.REGISTER_COLLECT_BIRTH_DATE in DRIVER_TRANSITIONS[DriverState.REGISTER_COLLECT_NAME]
        assert DriverState.REGISTER_COLLECT_VEHICLE in DRIVER_TRANSITIONS[DriverState.REGISTER_COLLECT_BIRTH_DATE]
        assert DriverState.REGISTER_COLLECT_DRESS_CODE in DRIVER_TRANSITIONS[DriverState.REGISTER_COLLECT_VEHICLE]

    @pytest.mark.unit
    def test_dress_code_branches_to_verify_or_menu(self) -> None:
        """קוד לבוש → אימות (חרדי) או תפריט (חילוני)"""
        targets = DRIVER_TRANSITIONS[DriverState.REGISTER_COLLECT_DRESS_CODE]
        assert DriverState.VERIFY_COLLECT_SELFIE in targets
        assert DriverState.MENU in targets

    @pytest.mark.unit
    def test_menu_has_main_navigation(self) -> None:
        """תפריט ראשי צריך לכלול הגדרות, חיפוש ומנוי"""
        targets = DRIVER_TRANSITIONS[DriverState.MENU]
        assert DriverState.SETTINGS_VIEW in targets
        assert DriverState.SEARCH_CREATE_ORIGIN in targets
        assert DriverState.SUBSCRIPTION_VIEW in targets

    @pytest.mark.unit
    def test_all_settings_return_to_menu(self) -> None:
        """כל מצב הגדרות חייב לאפשר חזרה לתפריט"""
        settings_states = [s for s in DriverState if s.value.startswith("DRIVER.SETTINGS.")]
        for state in settings_states:
            targets = DRIVER_TRANSITIONS.get(state, [])
            assert DriverState.MENU in targets, \
                f"מצב {state.value} לא מאפשר חזרה לתפריט"

    @pytest.mark.unit
    def test_state_values_use_dot_notation(self) -> None:
        """כל ערכי DriverState חייבים להתחיל ב-DRIVER."""
        for state in DriverState:
            assert state.value.startswith("DRIVER."), \
                f"מצב {state.name} לא מתחיל ב-DRIVER.: {state.value}"


# ============================================================================
# בדיקות מודלים (DB)
# ============================================================================


class TestDriverProfileModel:
    """בדיקות מודל DriverProfile"""

    @pytest.mark.asyncio
    async def test_create_driver_profile(self, db_session, user_factory) -> None:
        """יצירת פרופיל נהג תקין"""
        user = await user_factory(
            phone_number="+972503333333",
            name="נהג בדיקה",
            role=UserRole.DRIVER,
        )
        profile = DriverProfile(
            user_id=user.id,
            birth_date=date(1990, 5, 15),
            vehicle_description="סיינה 2025 חדישה",
            vehicle_category=VehicleCategory.SEVEN_SEATER.value,
            dress_code=DressCode.SECULAR.value,
        )
        db_session.add(profile)
        await db_session.commit()
        await db_session.refresh(profile)

        assert profile.id is not None
        assert profile.user_id == user.id
        assert profile.birth_date == date(1990, 5, 15)
        assert profile.vehicle_category == "7_seater"
        assert profile.dress_code == "secular"
        assert profile.verification_status == "unverified"
        assert profile.subscription_status == "trial"

    @pytest.mark.asyncio
    async def test_driver_profile_unique_user_id(self, db_session, user_factory) -> None:
        """user_id חייב להיות ייחודי בטבלת driver_profiles"""
        user = await user_factory(
            phone_number="+972504444444",
            name="נהג כפול",
            role=UserRole.DRIVER,
        )
        profile1 = DriverProfile(
            user_id=user.id,
            birth_date=date(1985, 1, 1),
            vehicle_description="רכב 1",
            vehicle_category=VehicleCategory.CAR.value,
            dress_code=DressCode.SECULAR.value,
        )
        db_session.add(profile1)
        await db_session.commit()

        # ניסיון ליצור פרופיל שני לאותו משתמש
        profile2 = DriverProfile(
            user_id=user.id,
            birth_date=date(1990, 6, 1),
            vehicle_description="רכב 2",
            vehicle_category=VehicleCategory.VAN.value,
            dress_code=DressCode.MIXED.value,
        )
        db_session.add(profile2)
        with pytest.raises(Exception):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_driver_profile_subscription_fields(self, db_session, user_factory) -> None:
        """שדות מנוי אופציונליים (nullable)"""
        user = await user_factory(
            phone_number="+972505555555",
            name="נהג מנוי",
            role=UserRole.DRIVER,
        )
        now = datetime.utcnow()
        profile = DriverProfile(
            user_id=user.id,
            birth_date=date(1988, 3, 20),
            vehicle_description="טסלה מודל 3",
            vehicle_category=VehicleCategory.FOUR_SEATER.value,
            dress_code=DressCode.SECULAR.value,
            trial_starts_at=now,
            trial_expires_at=now + timedelta(days=7),
        )
        db_session.add(profile)
        await db_session.commit()
        await db_session.refresh(profile)

        assert profile.trial_starts_at is not None
        assert profile.trial_expires_at is not None
        assert profile.subscription_start_at is None
        assert profile.subscription_expires_at is None


class TestDriverSearchSettingsModel:
    """בדיקות מודל DriverSearchSettings"""

    @pytest.mark.asyncio
    async def test_create_with_defaults(self, db_session, user_factory) -> None:
        """הגדרות ברירת מחדל"""
        user = await user_factory(
            phone_number="+972506666666",
            name="נהג הגדרות",
            role=UserRole.DRIVER,
        )
        settings = DriverSearchSettings(user_id=user.id)
        db_session.add(settings)
        await db_session.commit()
        await db_session.refresh(settings)

        assert settings.vehicle_type_filter == "7_seater"
        assert settings.trip_type_filter == "any_distance"
        assert settings.show_deliveries is True
        assert settings.upcoming_timeframe == "all"
        assert settings.future_only_enabled is False
        assert settings.future_only_start_time is None

    @pytest.mark.asyncio
    async def test_update_settings(self, db_session, user_factory) -> None:
        """עדכון הגדרות חיפוש"""
        user = await user_factory(
            phone_number="+972507777777",
            name="נהג עדכון",
            role=UserRole.DRIVER,
        )
        settings = DriverSearchSettings(
            user_id=user.id,
            trip_type_filter=TripTypeFilter.LONG_DISTANCE.value,
            show_deliveries=False,
            future_only_enabled=True,
            future_only_start_time=time(18, 0),
        )
        db_session.add(settings)
        await db_session.commit()
        await db_session.refresh(settings)

        assert settings.trip_type_filter == "long_distance"
        assert settings.show_deliveries is False
        assert settings.future_only_enabled is True
        assert settings.future_only_start_time == time(18, 0)


class TestDriverSearchModel:
    """בדיקות מודל DriverSearch"""

    @pytest.mark.asyncio
    async def test_create_route_search(self, db_session, user_factory) -> None:
        """יצירת חיפוש מסלול (עיר → עיר)"""
        user = await user_factory(
            phone_number="+972508888888",
            name="נהג חיפוש",
            role=UserRole.DRIVER,
        )
        search = DriverSearch(
            user_id=user.id,
            origin_city="תל אביב",
            destination_city="ירושלים",
        )
        db_session.add(search)
        await db_session.commit()
        await db_session.refresh(search)

        assert search.origin_city == "תל אביב"
        assert search.destination_city == "ירושלים"
        assert search.is_area_search is False
        assert search.latitude is None
        assert search.longitude is None
        assert search.status == "active"

    @pytest.mark.asyncio
    async def test_create_area_search(self, db_session, user_factory) -> None:
        """יצירת חיפוש אזורי (רדיוס ממיקום)"""
        user = await user_factory(
            phone_number="+972509999999",
            name="נהג אזור",
            role=UserRole.DRIVER,
        )
        search = DriverSearch(
            user_id=user.id,
            origin_city="חיפה",
            destination_city="חיפה",
            is_area_search=True,
            latitude=32.7940,
            longitude=34.9896,
        )
        db_session.add(search)
        await db_session.commit()
        await db_session.refresh(search)

        assert search.is_area_search is True
        assert search.latitude is not None
        assert search.longitude is not None

    @pytest.mark.asyncio
    async def test_soft_delete_search(self, db_session, user_factory) -> None:
        """מחיקה רכה של חיפוש"""
        user = await user_factory(
            phone_number="+972501010101",
            name="נהג מחיקה",
            role=UserRole.DRIVER,
        )
        search = DriverSearch(
            user_id=user.id,
            origin_city="באר שבע",
            destination_city="אילת",
        )
        db_session.add(search)
        await db_session.commit()

        search.status = DriverSearchStatus.DELETED.value
        await db_session.commit()
        await db_session.refresh(search)
        assert search.status == "deleted"

    @pytest.mark.asyncio
    async def test_multiple_searches_per_user(self, db_session, user_factory) -> None:
        """משתמש יכול ליצור מספר חיפושים"""
        user = await user_factory(
            phone_number="+972501111112",
            name="נהג מרובה",
            role=UserRole.DRIVER,
        )
        cities = [
            ("תל אביב", "ירושלים"),
            ("חיפה", "תל אביב"),
            ("באר שבע", "אשדוד"),
        ]
        for origin, dest in cities:
            search = DriverSearch(
                user_id=user.id,
                origin_city=origin,
                destination_city=dest,
            )
            db_session.add(search)

        await db_session.commit()

        result = await db_session.execute(
            select(func.count(DriverSearch.id)).where(DriverSearch.user_id == user.id)
        )
        count = result.scalar()
        assert count == 3


class TestDriverSessionModel:
    """בדיקות מודל DriverSession"""

    @pytest.mark.asyncio
    async def test_create_session(self, db_session, user_factory) -> None:
        """יצירת סשן נהג"""
        user = await user_factory(
            phone_number="+972502020202",
            name="נהג סשן",
            role=UserRole.DRIVER,
        )
        session = DriverSession(user_id=user.id)
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        assert session.is_active is True
        assert session.reminder_sent_at is None
        assert session.session_start_at is not None
        assert session.last_message_at is not None

    @pytest.mark.asyncio
    async def test_session_unique_per_user(self, db_session, user_factory) -> None:
        """user_id חייב להיות ייחודי בטבלת driver_sessions"""
        user = await user_factory(
            phone_number="+972503030303",
            name="נהג סשן כפול",
            role=UserRole.DRIVER,
        )
        session1 = DriverSession(user_id=user.id)
        db_session.add(session1)
        await db_session.commit()

        session2 = DriverSession(user_id=user.id)
        db_session.add(session2)
        with pytest.raises(Exception):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_session_deactivation(self, db_session, user_factory) -> None:
        """ניתוק סשן — is_active=False"""
        user = await user_factory(
            phone_number="+972504040404",
            name="נהג ניתוק",
            role=UserRole.DRIVER,
        )
        session = DriverSession(user_id=user.id)
        db_session.add(session)
        await db_session.commit()

        session.is_active = False
        session.reminder_sent_at = datetime.utcnow()
        await db_session.commit()
        await db_session.refresh(session)

        assert session.is_active is False
        assert session.reminder_sent_at is not None


# ============================================================================
# בדיקות ולידציה (Pydantic)
# ============================================================================


class TestDriverProfileCreateSchema:
    """בדיקות סכמת יצירת פרופיל נהג"""

    @pytest.mark.unit
    def test_valid_profile(self) -> None:
        """פרופיל תקין"""
        profile = DriverProfileCreate(
            name="ישראל ישראלי",
            birth_date=date(1990, 5, 15),
            vehicle_description="סיינה 2025",
            vehicle_category="7_seater",
            dress_code="secular",
        )
        assert profile.name == "ישראל ישראלי"
        assert profile.vehicle_category == "7_seater"

    @pytest.mark.unit
    def test_age_too_young(self) -> None:
        """גיל מתחת ל-16 חייב להיכשל"""
        with pytest.raises(ValueError, match="16"):
            DriverProfileCreate(
                name="צעיר מדי",
                birth_date=date.today() - timedelta(days=365 * 15),
                vehicle_description="רכב",
                vehicle_category="car",
                dress_code="secular",
            )

    @pytest.mark.unit
    def test_age_too_old(self) -> None:
        """גיל מעל 99 חייב להיכשל"""
        with pytest.raises(ValueError, match="99"):
            DriverProfileCreate(
                name="מבוגר מדי",
                birth_date=date(1920, 1, 1),
                vehicle_description="רכב",
                vehicle_category="car",
                dress_code="secular",
            )

    @pytest.mark.unit
    def test_valid_age_16(self) -> None:
        """גיל 16 בדיוק צריך לעבור"""
        birth = date.today().replace(year=date.today().year - 16)
        profile = DriverProfileCreate(
            name="בן שש עשרה",
            birth_date=birth,
            vehicle_description="אופנוע",
            vehicle_category="motorcycle",
            dress_code="secular",
        )
        assert profile.birth_date == birth

    @pytest.mark.unit
    def test_invalid_vehicle_category(self) -> None:
        """קטגוריית רכב לא תקינה"""
        with pytest.raises(ValueError, match="קטגוריית רכב"):
            DriverProfileCreate(
                name="נהג",
                birth_date=date(1990, 1, 1),
                vehicle_description="רכב",
                vehicle_category="spaceship",
                dress_code="secular",
            )

    @pytest.mark.unit
    def test_invalid_dress_code(self) -> None:
        """קוד לבוש לא תקין"""
        with pytest.raises(ValueError, match="קוד לבוש"):
            DriverProfileCreate(
                name="נהג",
                birth_date=date(1990, 1, 1),
                vehicle_description="רכב",
                vehicle_category="car",
                dress_code="alien",
            )

    @pytest.mark.unit
    def test_empty_vehicle_description(self) -> None:
        """תיאור רכב ריק חייב להיכשל"""
        with pytest.raises(ValueError):
            DriverProfileCreate(
                name="נהג",
                birth_date=date(1990, 1, 1),
                vehicle_description="   ",
                vehicle_category="car",
                dress_code="secular",
            )

    @pytest.mark.unit
    def test_vehicle_description_too_long(self) -> None:
        """תיאור רכב ארוך מ-200 תווים חייב להיכשל"""
        with pytest.raises(ValueError, match="200"):
            DriverProfileCreate(
                name="נהג",
                birth_date=date(1990, 1, 1),
                vehicle_description="א" * 201,
                vehicle_category="car",
                dress_code="secular",
            )


class TestDriverSearchCreateSchema:
    """בדיקות סכמת יצירת חיפוש"""

    @pytest.mark.unit
    def test_valid_search(self) -> None:
        """חיפוש תקין"""
        search = DriverSearchCreate(
            origin_city="תל אביב",
            destination_city="ירושלים",
        )
        assert search.origin_city == "תל אביב"
        assert search.is_area_search is False

    @pytest.mark.unit
    def test_empty_city_fails(self) -> None:
        """עיר ריקה חייבת להיכשל"""
        with pytest.raises(ValueError):
            DriverSearchCreate(
                origin_city="   ",
                destination_city="ירושלים",
            )

    @pytest.mark.unit
    def test_city_too_long(self) -> None:
        """שם עיר מעל 100 תווים חייב להיכשל"""
        with pytest.raises(ValueError, match="100"):
            DriverSearchCreate(
                origin_city="א" * 101,
                destination_city="ירושלים",
            )

    @pytest.mark.unit
    def test_area_search_with_coords(self) -> None:
        """חיפוש אזורי עם קואורדינטות"""
        search = DriverSearchCreate(
            origin_city="חיפה",
            destination_city="חיפה",
            is_area_search=True,
            latitude=32.7940,
            longitude=34.9896,
        )
        assert search.is_area_search is True
        assert search.latitude == 32.7940


class TestDriverSearchSettingsUpdateSchema:
    """בדיקות סכמת עדכון הגדרות חיפוש"""

    @pytest.mark.unit
    def test_partial_update(self) -> None:
        """עדכון חלקי — רק שדות שסופקו"""
        update = DriverSearchSettingsUpdate(
            trip_type_filter="long_distance",
            show_deliveries=False,
        )
        assert update.trip_type_filter == "long_distance"
        assert update.show_deliveries is False
        assert update.vehicle_type_filter is None

    @pytest.mark.unit
    def test_invalid_trip_type(self) -> None:
        """סוג נסיעה לא תקין"""
        with pytest.raises(ValueError, match="סוג נסיעה"):
            DriverSearchSettingsUpdate(trip_type_filter="teleport")

    @pytest.mark.unit
    def test_invalid_timeframe(self) -> None:
        """מסגרת זמן לא תקינה"""
        with pytest.raises(ValueError, match="מסגרת זמן"):
            DriverSearchSettingsUpdate(upcoming_timeframe="forever")

    @pytest.mark.unit
    def test_invalid_vehicle_type_filter(self) -> None:
        """סוג רכב לא תקין"""
        with pytest.raises(ValueError, match="סוג רכב"):
            DriverSearchSettingsUpdate(vehicle_type_filter="submarine")

    @pytest.mark.unit
    def test_future_only_with_non_all_timeframe_fails(self) -> None:
        """future_only_enabled=True עם מסגרת זמן שאינה 'all' חייב להיכשל"""
        with pytest.raises(ValueError, match="עתידי בלבד"):
            DriverSearchSettingsUpdate(
                future_only_enabled=True,
                upcoming_timeframe="1_hour",
            )

    @pytest.mark.unit
    def test_future_only_with_all_timeframe_succeeds(self) -> None:
        """future_only_enabled=True עם מסגרת זמן 'all' חייב להצליח"""
        update = DriverSearchSettingsUpdate(
            future_only_enabled=True,
            upcoming_timeframe="all",
        )
        assert update.future_only_enabled is True
        assert update.upcoming_timeframe == "all"

    @pytest.mark.unit
    def test_future_only_without_timeframe_succeeds(self) -> None:
        """future_only_enabled=True ללא מסגרת זמן (עדכון חלקי) חייב להצליח"""
        update = DriverSearchSettingsUpdate(future_only_enabled=True)
        assert update.future_only_enabled is True
        assert update.upcoming_timeframe is None

    @pytest.mark.unit
    def test_validate_against_existing_timeframe_change_blocks_violation(self) -> None:
        """שינוי timeframe ל-non-all כש-DB מכיל future_only=True חייב להיכשל"""
        update = DriverSearchSettingsUpdate(upcoming_timeframe="1_hour")
        with pytest.raises(ValueError, match="עתידי בלבד"):
            update.validate_against_existing(
                existing_future_only_enabled=True,
                existing_upcoming_timeframe="all",
            )

    @pytest.mark.unit
    def test_validate_against_existing_future_only_change_blocks_violation(self) -> None:
        """הפעלת future_only כש-DB מכיל timeframe שאינו all חייב להיכשל"""
        update = DriverSearchSettingsUpdate(future_only_enabled=True)
        with pytest.raises(ValueError, match="עתידי בלבד"):
            update.validate_against_existing(
                existing_future_only_enabled=False,
                existing_upcoming_timeframe="1_hour",
            )

    @pytest.mark.unit
    def test_validate_against_existing_valid_combination_passes(self) -> None:
        """שינוי timeframe ל-non-all כש-DB מכיל future_only=False חייב לעבור"""
        update = DriverSearchSettingsUpdate(upcoming_timeframe="2_hours")
        # לא אמור לזרוק שגיאה
        update.validate_against_existing(
            existing_future_only_enabled=False,
            existing_upcoming_timeframe="all",
        )


class TestDriverSearchCoordinateValidation:
    """בדיקות ולידציית קואורדינטות בחיפוש"""

    @pytest.mark.unit
    def test_area_search_without_coords_fails(self) -> None:
        """חיפוש אזורי ללא קואורדינטות חייב להיכשל"""
        with pytest.raises(ValueError, match="latitude"):
            DriverSearchCreate(
                origin_city="חיפה",
                destination_city="חיפה",
                is_area_search=True,
            )

    @pytest.mark.unit
    def test_area_search_with_only_lat_fails(self) -> None:
        """חיפוש אזורי עם latitude בלבד חייב להיכשל"""
        with pytest.raises(ValueError, match="latitude"):
            DriverSearchCreate(
                origin_city="חיפה",
                destination_city="חיפה",
                is_area_search=True,
                latitude=32.7940,
            )

    @pytest.mark.unit
    def test_area_search_with_only_lng_fails(self) -> None:
        """חיפוש אזורי עם longitude בלבד חייב להיכשל"""
        with pytest.raises(ValueError, match="latitude"):
            DriverSearchCreate(
                origin_city="חיפה",
                destination_city="חיפה",
                is_area_search=True,
                longitude=34.9896,
            )

    @pytest.mark.unit
    def test_route_search_with_coords_fails(self) -> None:
        """חיפוש מסלול עם קואורדינטות חייב להיכשל"""
        with pytest.raises(ValueError, match="קואורדינטות"):
            DriverSearchCreate(
                origin_city="תל אביב",
                destination_city="ירושלים",
                is_area_search=False,
                latitude=32.0,
                longitude=34.0,
            )

    @pytest.mark.unit
    def test_area_search_with_both_coords_passes(self) -> None:
        """חיפוש אזורי עם שתי קואורדינטות — תקין"""
        search = DriverSearchCreate(
            origin_city="חיפה",
            destination_city="חיפה",
            is_area_search=True,
            latitude=32.7940,
            longitude=34.9896,
        )
        assert search.latitude == 32.7940
        assert search.longitude == 34.9896
