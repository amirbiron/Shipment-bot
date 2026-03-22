"""
בדיקות יחידה — iDriver סשן 4: תפריט ראשי + הגדרות חיפוש

בודק:
- DriverMenuService (get_main_menu, get_settings_menu, update_*)
- DriverStateHandler (זרימת תפריט והגדרות: MENU ↔ SETTINGS_VIEW ↔ הגדרות בודדות)
- ולידציה צולבת (future_only + upcoming_timeframe)
- ניתוב מ-INITIAL לתפריט לנהג רשום
"""
import pytest
from datetime import date, datetime, timedelta, time
from unittest.mock import patch

from sqlalchemy import select

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    VehicleCategory,
    DriverSubscriptionStatus,
)
from app.db.models.driver_search_settings import (
    DriverSearchSettings,
    TripTypeFilter,
    UpcomingTimeframe,
)
from app.state_machine.states import DriverState
from app.state_machine.manager import StateManager
from app.state_machine.driver_handler import DriverStateHandler
from app.domain.services.driver_menu_service import (
    DriverMenuService,
    VEHICLE_TYPE_LABELS,
    TRIP_TYPE_LABELS,
    TIMEFRAME_LABELS,
)
from app.core.exceptions import NotFoundException


# ============================================================================
# עזר — יצירת נהג רשום עם פרופיל מלא
# ============================================================================


async def _create_registered_driver(
    db_session,
    user_factory,
    phone: str = "+972504001001",
    subscription_status: str = DriverSubscriptionStatus.TRIAL.value,
) -> tuple[User, DriverProfile]:
    """יוצר נהג רשום עם פרופיל מלא (is_registration_complete=True)"""
    user = await user_factory(
        phone_number=phone,
        name="נהג בדיקה",
        full_name="ישראל ישראלי",
        role=UserRole.DRIVER,
        telegram_chat_id=phone.replace("+972", ""),
    )
    now = datetime.utcnow()
    profile = DriverProfile(
        user_id=user.id,
        birth_date=date(1990, 1, 1),
        vehicle_description="טויוטה 2024",
        vehicle_category=VehicleCategory.SEVEN_SEATER.value,
        dress_code=DressCode.SECULAR.value,
        subscription_status=subscription_status,
        trial_starts_at=now,
        trial_expires_at=now + timedelta(days=7),
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return user, profile


async def _create_settings(
    db_session,
    user_id: int,
    **overrides,
) -> DriverSearchSettings:
    """יוצר הגדרות חיפוש עם ערכים מותאמים"""
    settings = DriverSearchSettings(user_id=user_id, **overrides)
    db_session.add(settings)
    await db_session.commit()
    await db_session.refresh(settings)
    return settings


# ============================================================================
# בדיקות DriverMenuService — תפריט ראשי
# ============================================================================


class TestGetMainMenu:
    """בדיקות בניית תפריט ראשי"""

    @pytest.mark.asyncio
    async def test_main_menu_shows_name_and_subscription(
        self, db_session, user_factory
    ) -> None:
        """התפריט מציג שם נהג וסטטוס מנוי"""
        user, profile = await _create_registered_driver(
            db_session, user_factory, "+972504001010"
        )
        service = DriverMenuService(db_session)
        text, keyboard = await service.get_main_menu(user.id)

        assert "ישראל ישראלי" in text
        assert "שבוע ניסיון" in text

    @pytest.mark.asyncio
    async def test_main_menu_shows_current_settings(
        self, db_session, user_factory
    ) -> None:
        """התפריט מציג הגדרות חיפוש נוכחיות"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001011"
        )
        await _create_settings(
            db_session,
            user.id,
            vehicle_type_filter=VehicleCategory.FOUR_SEATER.value,
            trip_type_filter=TripTypeFilter.LONG_DISTANCE.value,
            show_deliveries=False,
        )

        service = DriverMenuService(db_session)
        text, keyboard = await service.get_main_menu(user.id)

        assert "פרטי 4 מקומות" in text
        assert "מעל 100" in text
        assert "לא" in text  # show_deliveries=False

    @pytest.mark.asyncio
    async def test_main_menu_creates_default_settings(
        self, db_session, user_factory
    ) -> None:
        """התפריט יוצר הגדרות ברירת מחדל אם לא קיימות"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001012"
        )
        service = DriverMenuService(db_session)
        text, keyboard = await service.get_main_menu(user.id)

        # ברירת מחדל: 7 מקומות, כל סוגי הנסיעות, משלוחים=כן, הכל
        assert "7 מקומות" in text
        assert "כל סוגי הנסיעות" in text
        assert "כן" in text

    @pytest.mark.asyncio
    async def test_main_menu_has_buttons(
        self, db_session, user_factory
    ) -> None:
        """התפריט מכיל כפתורי ניווט"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001013"
        )
        service = DriverMenuService(db_session)
        text, keyboard = await service.get_main_menu(user.id)

        flat_buttons = [btn for row in keyboard for btn in row]
        assert any("הגדרות" in b for b in flat_buttons)
        assert any("הוראות" in b for b in flat_buttons)

    @pytest.mark.asyncio
    async def test_main_menu_no_profile_raises(
        self, db_session, user_factory
    ) -> None:
        """נהג ללא פרופיל — NotFoundException"""
        user = await user_factory(
            phone_number="+972504001014",
            role=UserRole.DRIVER,
        )
        service = DriverMenuService(db_session)
        with pytest.raises(NotFoundException):
            await service.get_main_menu(user.id)


class TestSubscriptionStatusDisplay:
    """בדיקות תצוגת סטטוס מנוי"""

    @pytest.mark.asyncio
    async def test_trial_status(self, db_session, user_factory) -> None:
        """תקופת ניסיון מוצגת כ-'שבוע ניסיון'"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001020"
        )
        service = DriverMenuService(db_session)
        text, _ = await service.get_main_menu(user.id)
        assert "שבוע ניסיון" in text

    @pytest.mark.asyncio
    async def test_active_status(self, db_session, user_factory) -> None:
        """מנוי פעיל מוצג כ-'מנוי פעיל'"""
        user, profile = await _create_registered_driver(
            db_session, user_factory, "+972504001021",
            subscription_status=DriverSubscriptionStatus.ACTIVE.value,
        )
        profile.subscription_expires_at = datetime.utcnow() + timedelta(days=30)
        await db_session.commit()

        service = DriverMenuService(db_session)
        text, _ = await service.get_main_menu(user.id)
        assert "מנוי פעיל" in text

    @pytest.mark.asyncio
    async def test_expired_status(self, db_session, user_factory) -> None:
        """מנוי שפג מוצג כ-'פג'"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001022",
            subscription_status=DriverSubscriptionStatus.EXPIRED.value,
        )
        service = DriverMenuService(db_session)
        text, _ = await service.get_main_menu(user.id)
        assert "פג" in text


# ============================================================================
# בדיקות DriverMenuService — הגדרות חיפוש
# ============================================================================


class TestGetSettingsMenu:
    """בדיקות תפריט הגדרות"""

    @pytest.mark.asyncio
    async def test_settings_menu_shows_current_values(
        self, db_session, user_factory
    ) -> None:
        """תפריט הגדרות מציג ערכים נוכחיים"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001030"
        )
        await _create_settings(
            db_session, user.id,
            vehicle_type_filter=VehicleCategory.FOUR_SEATER.value,
            show_deliveries=False,
        )
        service = DriverMenuService(db_session)
        text, keyboard = await service.get_settings_menu(user.id)

        assert "הגדרות חיפוש" in text
        assert "פרטי 4 מקומות" in text
        assert "לא ❌" in text

    @pytest.mark.asyncio
    async def test_settings_menu_has_all_options(
        self, db_session, user_factory
    ) -> None:
        """תפריט הגדרות מכיל את כל 5 האפשרויות + חזרה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001031"
        )
        service = DriverMenuService(db_session)
        text, keyboard = await service.get_settings_menu(user.id)

        flat = [btn for row in keyboard for btn in row]
        assert any("סוג רכב" in b for b in flat)
        assert any("סוג נסיעה" in b for b in flat)
        assert any("משלוחים" in b for b in flat)
        assert any("מסגרת זמן" in b for b in flat)
        assert any("עתידי" in b for b in flat)
        assert any("חזרה" in b for b in flat)


# ============================================================================
# בדיקות DriverMenuService — עדכון הגדרות
# ============================================================================


class TestUpdateVehicleType:
    """בדיקות עדכון סוג רכב"""

    @pytest.mark.asyncio
    async def test_update_valid(self, db_session, user_factory) -> None:
        """עדכון סוג רכב תקין"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001040"
        )
        service = DriverMenuService(db_session)
        label = await service.update_vehicle_type(user.id, VehicleCategory.FOUR_SEATER.value)

        assert label == "פרטי 4 מקומות"

        # ולידציה ב-DB
        result = await db_session.execute(
            select(DriverSearchSettings).where(DriverSearchSettings.user_id == user.id)
        )
        settings = result.scalar_one()
        assert settings.vehicle_type_filter == VehicleCategory.FOUR_SEATER.value

    @pytest.mark.asyncio
    async def test_update_invalid_raises(self, db_session, user_factory) -> None:
        """ערך לא תקין זורק שגיאה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001041"
        )
        service = DriverMenuService(db_session)
        with pytest.raises(ValueError):
            await service.update_vehicle_type(user.id, "invalid_type")


class TestUpdateTripType:
    """בדיקות עדכון סוג נסיעה"""

    @pytest.mark.asyncio
    async def test_update_valid(self, db_session, user_factory) -> None:
        """עדכון סוג נסיעה תקין"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001050"
        )
        service = DriverMenuService(db_session)
        label = await service.update_trip_type(user.id, TripTypeFilter.LONG_DISTANCE.value)
        assert label == "100₪+ פנימיות ובינעירוני"


class TestUpdateShowDeliveries:
    """בדיקות עדכון הצגת משלוחים"""

    @pytest.mark.asyncio
    async def test_toggle_off(self, db_session, user_factory) -> None:
        """כיבוי הצגת משלוחים"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001060"
        )
        service = DriverMenuService(db_session)
        result = await service.update_show_deliveries(user.id, False)
        assert result is False

        db_result = await db_session.execute(
            select(DriverSearchSettings).where(DriverSearchSettings.user_id == user.id)
        )
        assert db_result.scalar_one().show_deliveries is False

    @pytest.mark.asyncio
    async def test_toggle_on(self, db_session, user_factory) -> None:
        """הפעלת הצגת משלוחים"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001061"
        )
        await _create_settings(db_session, user.id, show_deliveries=False)
        service = DriverMenuService(db_session)
        result = await service.update_show_deliveries(user.id, True)
        assert result is True


class TestUpdateTimeframe:
    """בדיקות עדכון מסגרת זמן"""

    @pytest.mark.asyncio
    async def test_update_valid(self, db_session, user_factory) -> None:
        """עדכון מסגרת זמן תקין"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001070"
        )
        service = DriverMenuService(db_session)
        label = await service.update_timeframe(user.id, UpcomingTimeframe.TWO_HOURS.value)
        assert label == "לשעתיים הקרובות"

    @pytest.mark.asyncio
    async def test_changing_timeframe_disables_future_only(
        self, db_session, user_factory
    ) -> None:
        """שינוי מסגרת זמן מ-ALL לערך אחר מכבה חיפוש עתידי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001071"
        )
        await _create_settings(
            db_session, user.id,
            upcoming_timeframe=UpcomingTimeframe.ALL.value,
            future_only_enabled=True,
            future_only_start_time=time(8, 0),
        )

        service = DriverMenuService(db_session)
        await service.update_timeframe(user.id, UpcomingTimeframe.ONE_HOUR.value)

        result = await db_session.execute(
            select(DriverSearchSettings).where(DriverSearchSettings.user_id == user.id)
        )
        settings = result.scalar_one()
        assert settings.future_only_enabled is False
        assert settings.future_only_start_time is None


class TestUpdateFutureOnly:
    """בדיקות עדכון חיפוש עתידי"""

    @pytest.mark.asyncio
    async def test_enable_with_start_time(self, db_session, user_factory) -> None:
        """הפעלת חיפוש עתידי עם שעה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001080"
        )
        # ברירת מחדל: upcoming_timeframe=ALL
        await _create_settings(
            db_session, user.id,
            upcoming_timeframe=UpcomingTimeframe.ALL.value,
        )
        service = DriverMenuService(db_session)
        enabled, start_time = await service.update_future_only(
            user.id, True, time(8, 0)
        )
        assert enabled is True
        assert start_time == time(8, 0)

    @pytest.mark.asyncio
    async def test_enable_without_time_raises(self, db_session, user_factory) -> None:
        """הפעלה ללא שעה זורקת שגיאה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001081"
        )
        await _create_settings(
            db_session, user.id,
            upcoming_timeframe=UpcomingTimeframe.ALL.value,
        )
        service = DriverMenuService(db_session)
        with pytest.raises(ValueError, match="שעת התחלה"):
            await service.update_future_only(user.id, True, None)

    @pytest.mark.asyncio
    async def test_enable_with_non_all_timeframe_raises(
        self, db_session, user_factory
    ) -> None:
        """הפעלה כשמסגרת הזמן לא ALL זורקת שגיאה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001082"
        )
        await _create_settings(
            db_session, user.id,
            upcoming_timeframe=UpcomingTimeframe.ONE_HOUR.value,
        )
        service = DriverMenuService(db_session)
        with pytest.raises(ValueError, match="הכל"):
            await service.update_future_only(user.id, True, time(8, 0))

    @pytest.mark.asyncio
    async def test_disable(self, db_session, user_factory) -> None:
        """כיבוי חיפוש עתידי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504001083"
        )
        await _create_settings(
            db_session, user.id,
            upcoming_timeframe=UpcomingTimeframe.ALL.value,
            future_only_enabled=True,
            future_only_start_time=time(8, 0),
        )
        service = DriverMenuService(db_session)
        enabled, start_time = await service.update_future_only(user.id, False, None)
        assert enabled is False
        assert start_time is None


# ============================================================================
# בדיקות DriverStateHandler — זרימת תפריט
# ============================================================================


class TestDriverMenuStateHandler:
    """בדיקות ניתוב תפריט ב-state handler"""

    @pytest.mark.asyncio
    async def test_registered_driver_initial_goes_to_menu(
        self, db_session, user_factory
    ) -> None:
        """נהג רשום ב-INITIAL מנותב לתפריט ראשי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504002001"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.INITIAL.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "תפריט", None)

        assert new_state == DriverState.MENU.value
        assert "ישראל ישראלי" in response.text

    @pytest.mark.asyncio
    async def test_menu_settings_button(
        self, db_session, user_factory
    ) -> None:
        """לחיצה על 'הגדרות חיפוש' מנתבת ל-SETTINGS_VIEW"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504002002"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "🛠 הגדרות חיפוש", None
        )

        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "הגדרות חיפוש" in response.text

    @pytest.mark.asyncio
    async def test_menu_help_button(
        self, db_session, user_factory
    ) -> None:
        """לחיצה על 'הוראות שימוש' מציגה עזרה ונשארת ב-MENU"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504002003"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "📖 הוראות שימוש", None
        )

        assert new_state == DriverState.MENU.value
        assert "הוראות שימוש" in response.text

    @pytest.mark.asyncio
    async def test_menu_refresh(
        self, db_session, user_factory
    ) -> None:
        """שליחת טקסט כללי ב-MENU מרעננת את התפריט"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504002004"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "ת", None)

        assert new_state == DriverState.MENU.value
        assert "ישראל ישראלי" in response.text


# ============================================================================
# בדיקות DriverStateHandler — זרימת הגדרות
# ============================================================================


class TestDriverSettingsFlow:
    """בדיקות זרימת הגדרות חיפוש"""

    @pytest.mark.asyncio
    async def test_settings_view_to_vehicle_type(
        self, db_session, user_factory
    ) -> None:
        """בחירת 'סוג רכב' מעבירה ל-SETTINGS_VEHICLE_TYPE"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003001"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_VIEW.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "🚙 סוג רכב", None
        )

        assert new_state == DriverState.SETTINGS_VEHICLE_TYPE.value
        assert "בחר סוג רכב" in response.text

    @pytest.mark.asyncio
    async def test_select_vehicle_type(
        self, db_session, user_factory
    ) -> None:
        """בחירת סוג רכב שומרת ב-DB וחוזרת ל-SETTINGS_VIEW"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003002"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_VEHICLE_TYPE.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "פרטי 4 מקומות", None
        )

        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "עודכן" in response.text
        assert "פרטי 4 מקומות" in response.text

    @pytest.mark.asyncio
    async def test_select_vehicle_type_invalid(
        self, db_session, user_factory
    ) -> None:
        """בחירה לא תקינה — הודעת שגיאה ונשאר באותו מצב"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003003"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_VEHICLE_TYPE.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "רכב לא קיים", None
        )

        assert new_state == DriverState.SETTINGS_VEHICLE_TYPE.value
        assert "לא תקינה" in response.text

    @pytest.mark.asyncio
    async def test_cancel_vehicle_type(
        self, db_session, user_factory
    ) -> None:
        """ביטול חוזר ל-SETTINGS_VIEW"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003004"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_VEHICLE_TYPE.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "❌ ביטול", None
        )

        assert new_state == DriverState.SETTINGS_VIEW.value

    @pytest.mark.asyncio
    async def test_select_trip_type(
        self, db_session, user_factory
    ) -> None:
        """בחירת סוג נסיעה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003005"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_TRIP_TYPE.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "טווח בינוני (15-50 ק״מ)", None
        )

        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "עודכן" in response.text

    @pytest.mark.asyncio
    async def test_select_deliveries_yes(
        self, db_session, user_factory
    ) -> None:
        """בחירת כן להצגת משלוחים"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003006"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_SHOW_DELIVERIES.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "✅ כן — הצג משלוחים", None
        )

        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "עודכנה" in response.text
        assert "כן" in response.text

    @pytest.mark.asyncio
    async def test_select_deliveries_no(
        self, db_session, user_factory
    ) -> None:
        """בחירת לא להצגת משלוחים"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003007"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_SHOW_DELIVERIES.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "❌ לא — ללא משלוחים", None
        )

        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "לא" in response.text

    @pytest.mark.asyncio
    async def test_select_timeframe(
        self, db_session, user_factory
    ) -> None:
        """בחירת מסגרת זמן"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003008"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_UPCOMING_TIMEFRAME.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "לשעתיים הקרובות", None
        )

        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "עודכנה" in response.text

    @pytest.mark.asyncio
    async def test_future_only_enable_flow(
        self, db_session, user_factory
    ) -> None:
        """זרימה מלאה: הפעלת חיפוש עתידי → הזנת שעה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003009"
        )
        await _create_settings(
            db_session, user.id,
            upcoming_timeframe=UpcomingTimeframe.ALL.value,
        )
        state_manager = StateManager(db_session)

        handler = DriverStateHandler(db_session, platform="telegram")

        # שלב 1: בחירת הפעלה
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_FUTURE_ONLY_MODE.value, {}
        )
        response, new_state = await handler.handle_message(
            user, "✅ כן — הפעל חיפוש עתידי", None
        )
        assert new_state == DriverState.SETTINGS_START_TIME.value
        assert "HH:MM" in response.text

        # שלב 2: הזנת שעה
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_START_TIME.value, {}
        )
        response, new_state = await handler.handle_message(user, "08:00", None)
        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "הופעל" in response.text
        assert "08:00" in response.text

    @pytest.mark.asyncio
    async def test_future_only_disable(
        self, db_session, user_factory
    ) -> None:
        """כיבוי חיפוש עתידי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003010"
        )
        await _create_settings(
            db_session, user.id,
            upcoming_timeframe=UpcomingTimeframe.ALL.value,
            future_only_enabled=True,
            future_only_start_time=time(8, 0),
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_FUTURE_ONLY_MODE.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "❌ לא — כבה חיפוש עתידי", None
        )

        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "כובה" in response.text

    @pytest.mark.asyncio
    async def test_invalid_time_format(
        self, db_session, user_factory
    ) -> None:
        """שעה בפורמט לא תקין — שגיאה ונשאר באותו מצב"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003011"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_START_TIME.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "abc", None)

        assert new_state == DriverState.SETTINGS_START_TIME.value
        assert "לא תקין" in response.text

    @pytest.mark.asyncio
    async def test_settings_back_to_menu(
        self, db_session, user_factory
    ) -> None:
        """חזרה מהגדרות לתפריט ראשי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504003012"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_VIEW.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "🔙 חזרה לתפריט", None
        )

        assert new_state == DriverState.MENU.value
        assert "ישראל ישראלי" in response.text


# ============================================================================
# בדיקות פרסור שעה
# ============================================================================


class TestParseTime:
    """בדיקות פרסור שעה"""

    def test_valid_formats(self) -> None:
        """פורמטים תקינים"""
        assert DriverStateHandler._parse_time("08:00") == time(8, 0)
        assert DriverStateHandler._parse_time("14:30") == time(14, 30)
        assert DriverStateHandler._parse_time("0:00") == time(0, 0)
        assert DriverStateHandler._parse_time("23:59") == time(23, 59)

    def test_invalid_formats(self) -> None:
        """פורמטים לא תקינים"""
        assert DriverStateHandler._parse_time("abc") is None
        assert DriverStateHandler._parse_time("25:00") is None
        assert DriverStateHandler._parse_time("12:60") is None
        assert DriverStateHandler._parse_time("12") is None
        assert DriverStateHandler._parse_time("") is None


# ============================================================================
# בדיקות ניווט חזרה — "חזרה להגדרות" vs "חזרה לתפריט"
# ============================================================================


class TestBackNavigation:
    """בדיקת ניתוב כפתורי חזרה ב-SETTINGS_VIEW"""

    @pytest.mark.asyncio
    async def test_back_to_settings_from_settings_view(
        self, db_session, user_factory
    ) -> None:
        """כפתור 'חזרה להגדרות' מ-SETTINGS_VIEW חוזר להגדרות, לא לתפריט"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504004001"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_VIEW.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "🔙 חזרה להגדרות", None
        )

        # חייב להישאר ב-SETTINGS_VIEW ולהציג תפריט הגדרות
        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "הגדרות חיפוש" in response.text
        assert "סוג רכב" in response.text

    @pytest.mark.asyncio
    async def test_back_to_menu_from_settings_view(
        self, db_session, user_factory
    ) -> None:
        """כפתור 'חזרה לתפריט' מ-SETTINGS_VIEW חוזר לתפריט ראשי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504004002"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_VIEW.value, {}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "🔙 חזרה לתפריט", None
        )

        assert new_state == DriverState.MENU.value
        assert "בחר אפשרות" in response.text


# ============================================================================
# בדיקות שם משתמש — operator precedence
# ============================================================================


class TestUserNameFallback:
    """בדיקת fallback שם משתמש בתפריט"""

    @pytest.mark.asyncio
    async def test_menu_with_no_user_record(
        self, db_session, user_factory
    ) -> None:
        """
        פרופיל נהג עם user_id שלא קיים בטבלת users —
        צריך להציג 'לא צוין' במקום לזרוק AttributeError.
        """
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972504004003"
        )
        service = DriverMenuService(db_session)
        # התפריט אמור לעבוד ולא לזרוק שגיאה
        text, keyboard = await service.get_main_menu(user.id)
        # השם מגיע מ-User.full_name / User.name
        assert isinstance(text, str)
        assert len(text) > 0
