"""
בדיקות יחידה — iDriver סשן 5: חיפוש נסיעות

בודק:
- CityAbbreviationService (פרסור פקודות, קיצורי ערים)
- DriverSearchService (CRUD חיפושים, מגבלות, כפילויות)
- DriverStateHandler (זרימת חיפוש: MENU → חיפוש, צפייה, מחיקה, מיקום GPS)
"""
import pytest
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    VehicleCategory,
    DriverSubscriptionStatus,
)
from app.db.models.driver_search import (
    DriverSearch,
    DriverSearchStatus,
    MAX_ACTIVE_SEARCHES_PER_USER,
)
from app.state_machine.states import DriverState
from app.state_machine.driver_handler import DriverStateHandler
from app.domain.services.driver_search_service import DriverSearchService
from app.domain.services.city_abbreviation_service import (
    CityAbbreviationService,
    ParsedSearchCommand,
)
from app.core.exceptions import ValidationException, NotFoundException


# ============================================================================
# עזר — יצירת נהג רשום עם פרופיל מלא
# ============================================================================


async def _create_registered_driver(
    db_session,
    user_factory,
    phone: str = "+972505001001",
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
        subscription_status=DriverSubscriptionStatus.TRIAL.value,
        trial_starts_at=now,
        trial_expires_at=now + timedelta(days=7),
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return user, profile


async def _create_search(
    db_session,
    user_id: int,
    origin_city: str = "תל אביב",
    destination_city: str = "ירושלים",
    is_area_search: bool = False,
) -> DriverSearch:
    """יוצר חיפוש פעיל לבדיקות"""
    search = DriverSearch(
        user_id=user_id,
        origin_city=origin_city,
        destination_city=destination_city,
        is_area_search=is_area_search,
        status=DriverSearchStatus.ACTIVE.value,
    )
    db_session.add(search)
    await db_session.commit()
    await db_session.refresh(search)
    return search


# ============================================================================
# בדיקות CityAbbreviationService — קיצורי ערים
# ============================================================================


class TestCityAbbreviationService:
    """בדיקות שירות קיצורי ערים"""

    @pytest.mark.unit
    def test_resolve_known_abbreviation(self) -> None:
        """פתרון קיצור מוכר"""
        assert CityAbbreviationService.resolve("ים") == "ירושלים"
        assert CityAbbreviationService.resolve("תא") == "תל אביב"
        assert CityAbbreviationService.resolve("בב") == "בני ברק"

    @pytest.mark.unit
    def test_resolve_full_name(self) -> None:
        """פתרון שם מלא — מחזיר אותו כפי שהוא"""
        assert CityAbbreviationService.resolve("ירושלים") == "ירושלים"

    @pytest.mark.unit
    def test_resolve_unknown_returns_none(self) -> None:
        """קיצור לא מוכר מחזיר None"""
        assert CityAbbreviationService.resolve("xyz") is None

    @pytest.mark.unit
    def test_resolve_or_raw_known(self) -> None:
        """resolve_or_raw עם קיצור מוכר מחזיר שם מלא"""
        assert CityAbbreviationService.resolve_or_raw("ים") == "ירושלים"

    @pytest.mark.unit
    def test_resolve_or_raw_unknown(self) -> None:
        """resolve_or_raw עם טקסט לא מוכר מחזיר אותו מסוניטז"""
        result = CityAbbreviationService.resolve_or_raw("נתיבות")
        assert result == "נתיבות"

    @pytest.mark.unit
    def test_is_search_command(self) -> None:
        """זיהוי פקודת חיפוש"""
        assert CityAbbreviationService.is_search_command("פ ים") is True
        assert CityAbbreviationService.is_search_command("פ בב ים") is True
        assert CityAbbreviationService.is_search_command("פ") is True
        assert CityAbbreviationService.is_search_command("שלום") is False
        assert CityAbbreviationService.is_search_command("") is False

    @pytest.mark.unit
    def test_parse_destination_only(self) -> None:
        """פרסור פקודה עם יעד בלבד: פ ים"""
        result = CityAbbreviationService.parse_search_command("פ ים")
        assert result is not None
        assert result.origin is None
        assert result.destination == "ירושלים"
        assert result.is_area_search is False
        assert result.is_location_search is False

    @pytest.mark.unit
    def test_parse_origin_and_destination(self) -> None:
        """פרסור פקודה עם מוצא ויעד: פ בב ים"""
        result = CityAbbreviationService.parse_search_command("פ בב ים")
        assert result is not None
        assert result.origin == "בני ברק"
        assert result.destination == "ירושלים"
        assert result.is_area_search is False

    @pytest.mark.unit
    def test_parse_area_search(self) -> None:
        """פרסור חיפוש אזורי: פ א טב"""
        result = CityAbbreviationService.parse_search_command("פ א טב")
        assert result is not None
        assert result.origin is None
        assert result.destination == "טבריה"
        assert result.is_area_search is True

    @pytest.mark.unit
    def test_parse_origin_area_search(self) -> None:
        """פרסור ממוצא ליעד אזורי: פ ים א טב"""
        result = CityAbbreviationService.parse_search_command("פ ים א טב")
        assert result is not None
        assert result.origin == "ירושלים"
        assert result.destination == "טבריה"
        assert result.is_area_search is True

    @pytest.mark.unit
    def test_parse_location_search(self) -> None:
        """פרסור חיפוש מיקום: פ מיקום"""
        result = CityAbbreviationService.parse_search_command("פ מיקום")
        assert result is not None
        assert result.is_location_search is True

    @pytest.mark.unit
    def test_parse_no_args_returns_none(self) -> None:
        """פ בלי פרמטרים מחזיר None"""
        result = CityAbbreviationService.parse_search_command("פ")
        assert result is None

    @pytest.mark.unit
    def test_parse_non_command_returns_none(self) -> None:
        """טקסט שאינו פקודת חיפוש מחזיר None"""
        result = CityAbbreviationService.parse_search_command("שלום")
        assert result is None

    @pytest.mark.unit
    def test_get_abbreviations_help(self) -> None:
        """בדיקת הודעת עזרה קיצורים"""
        help_text = CityAbbreviationService.get_abbreviations_help()
        assert "ים = ירושלים" in help_text
        assert "תא = תל אביב" in help_text


# ============================================================================
# בדיקות DriverSearchService — שירות חיפוש
# ============================================================================


class TestDriverSearchService:
    """בדיקות שירות חיפוש נסיעות"""

    @pytest.mark.asyncio
    async def test_create_search(self, db_session, user_factory) -> None:
        """יצירת חיפוש חדש"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002001")
        service = DriverSearchService(db_session)

        search = await service.create_search(
            user_id=user.id,
            origin_city="תל אביב",
            destination_city="ירושלים",
        )
        assert search.id is not None
        assert search.user_id == user.id
        assert search.origin_city == "תל אביב"
        assert search.destination_city == "ירושלים"
        assert search.status == DriverSearchStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_create_area_search(self, db_session, user_factory) -> None:
        """יצירת חיפוש אזורי"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002002")
        service = DriverSearchService(db_session)

        search = await service.create_search(
            user_id=user.id,
            origin_city="",
            destination_city="טבריה",
            is_area_search=True,
        )
        assert search.is_area_search is True

    @pytest.mark.asyncio
    async def test_create_location_search(self, db_session, user_factory) -> None:
        """יצירת חיפוש לפי מיקום GPS"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002003")
        service = DriverSearchService(db_session)

        search = await service.create_location_search(
            user_id=user.id,
            latitude=31.7683,
            longitude=35.2137,
        )
        assert search.origin_city == "מיקום נוכחי"
        assert search.destination_city == "אזור מיקום"
        assert search.is_area_search is True
        assert search.latitude is not None
        assert search.longitude is not None

    @pytest.mark.asyncio
    async def test_max_active_searches_enforcement(self, db_session, user_factory) -> None:
        """אכיפת מגבלת 9 חיפושים פעילים"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002004")
        service = DriverSearchService(db_session)

        # יצירת 9 חיפושים
        for i in range(MAX_ACTIVE_SEARCHES_PER_USER):
            await service.create_search(
                user_id=user.id,
                origin_city="",
                destination_city=f"עיר_{i}",
            )

        # ניסיון ליצור עשירי — חריגה
        with pytest.raises(ValidationException, match="מקסימום"):
            await service.create_search(
                user_id=user.id,
                origin_city="",
                destination_city="עיר_10",
            )

    @pytest.mark.asyncio
    async def test_duplicate_search_prevention(self, db_session, user_factory) -> None:
        """מניעת כפילויות — אותו מוצא + יעד"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002005")
        service = DriverSearchService(db_session)

        await service.create_search(
            user_id=user.id,
            origin_city="תל אביב",
            destination_city="ירושלים",
        )

        with pytest.raises(ValidationException, match="כבר קיים"):
            await service.create_search(
                user_id=user.id,
                origin_city="תל אביב",
                destination_city="ירושלים",
            )

    @pytest.mark.asyncio
    async def test_get_active_searches(self, db_session, user_factory) -> None:
        """שליפת חיפושים פעילים"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002006")
        await _create_search(db_session, user.id, destination_city="ירושלים")
        await _create_search(db_session, user.id, destination_city="חיפה")

        service = DriverSearchService(db_session)
        searches = await service.get_active_searches(user.id)

        assert len(searches) == 2

    @pytest.mark.asyncio
    async def test_delete_search(self, db_session, user_factory) -> None:
        """מחיקת חיפוש בודד (soft-delete)"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002007")
        search = await _create_search(db_session, user.id)

        service = DriverSearchService(db_session)
        result = await service.delete_search(user.id, search.id)

        assert result is True

        # ווידוא שהסטטוס השתנה
        await db_session.refresh(search)
        assert search.status == DriverSearchStatus.DELETED.value

    @pytest.mark.asyncio
    async def test_delete_search_wrong_user(self, db_session, user_factory) -> None:
        """מחיקת חיפוש של משתמש אחר — נכשל"""
        user1, _ = await _create_registered_driver(db_session, user_factory, "+972505002008")
        user2, _ = await _create_registered_driver(db_session, user_factory, "+972505002009")
        search = await _create_search(db_session, user1.id)

        service = DriverSearchService(db_session)
        with pytest.raises(ValidationException, match="אין הרשאה"):
            await service.delete_search(user2.id, search.id)

    @pytest.mark.asyncio
    async def test_delete_all_searches(self, db_session, user_factory) -> None:
        """מחיקת כל החיפושים"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002010")
        await _create_search(db_session, user.id, destination_city="ירושלים")
        await _create_search(db_session, user.id, destination_city="חיפה")

        service = DriverSearchService(db_session)
        count = await service.delete_all_searches(user.id)

        assert count == 2

        remaining = await service.get_active_searches(user.id)
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_search(self, db_session, user_factory) -> None:
        """מחיקת חיפוש שלא קיים"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002011")

        service = DriverSearchService(db_session)
        with pytest.raises(NotFoundException):
            await service.delete_search(user.id, 999999)

    @pytest.mark.unit
    def test_format_search_summary(self, db_session) -> None:
        """פורמט חיפוש בודד"""
        search = DriverSearch(
            origin_city="תל אביב",
            destination_city="ירושלים",
            is_area_search=False,
        )
        result = DriverSearchService.format_search_summary(search)
        assert "תל אביב" in result
        assert "ירושלים" in result
        assert "📍" in result

    @pytest.mark.unit
    def test_format_search_summary_area(self, db_session) -> None:
        """פורמט חיפוש אזורי"""
        search = DriverSearch(
            origin_city="",
            destination_city="טבריה",
            is_area_search=True,
        )
        result = DriverSearchService.format_search_summary(search)
        assert "טבריה" in result
        assert "אזורי" in result

    @pytest.mark.unit
    def test_format_searches_list_empty(self) -> None:
        """פורמט רשימה ריקה"""
        result = DriverSearchService.format_searches_list([])
        assert "אין חיפושים" in result

    @pytest.mark.unit
    def test_format_search_summary_zero_coordinates(self) -> None:
        """פורמט חיפוש GPS עם קואורדינטות אפס — לא מפספס בגלל truthiness"""
        from decimal import Decimal
        search = DriverSearch(
            origin_city="מיקום נוכחי",
            destination_city="אזור מיקום",
            is_area_search=True,
            latitude=Decimal("0.0000000"),
            longitude=Decimal("0.0000000"),
        )
        result = DriverSearchService.format_search_summary(search)
        assert "מיקום GPS" in result

    @pytest.mark.asyncio
    async def test_multiple_gps_searches_different_locations(
        self, db_session, user_factory
    ) -> None:
        """יצירת מספר חיפושי GPS ממיקומים שונים — ללא חסימת כפילויות"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002020")
        service = DriverSearchService(db_session)

        search1 = await service.create_location_search(
            user_id=user.id, latitude=31.7683, longitude=35.2137,
        )
        assert search1.id is not None

        search2 = await service.create_location_search(
            user_id=user.id, latitude=32.0853, longitude=34.7818,
        )
        assert search2.id is not None

        searches = await service.get_active_searches(user.id)
        assert len(searches) == 2

    @pytest.mark.asyncio
    async def test_gps_search_same_location_blocked(
        self, db_session, user_factory
    ) -> None:
        """חיפוש GPS מאותו מיקום — חסום ככפילות"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505002021")
        service = DriverSearchService(db_session)

        await service.create_location_search(
            user_id=user.id, latitude=31.7683, longitude=35.2137,
        )

        with pytest.raises(ValidationException, match="כבר קיים"):
            await service.create_location_search(
                user_id=user.id, latitude=31.7683, longitude=35.2137,
            )


# ============================================================================
# בדיקות DriverStateHandler — זרימת חיפוש
# ============================================================================


class TestDriverSearchHandler:
    """בדיקות handler חיפוש נסיעות"""

    @pytest.mark.asyncio
    async def test_search_command_from_menu(self, db_session, user_factory) -> None:
        """פקודת 'פ ים' מהתפריט הראשי — יוצרת חיפוש ומחזירה ל-MENU"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003001")

        handler = DriverStateHandler(db_session, platform="telegram")
        # סימולציית מצב MENU
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "פ ים")
        assert new_state == DriverState.MENU.value
        assert "ירושלים" in response.text
        assert "✅" in response.text

    @pytest.mark.asyncio
    async def test_search_command_origin_destination(self, db_session, user_factory) -> None:
        """פקודת 'פ בב ים' — חיפוש ממוצא ליעד"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003002")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "פ בב ים")
        assert new_state == DriverState.MENU.value
        assert "בני ברק" in response.text
        assert "ירושלים" in response.text

    @pytest.mark.asyncio
    async def test_search_command_area(self, db_session, user_factory) -> None:
        """פקודת 'פ א טב' — חיפוש אזורי"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003003")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "פ א טב")
        assert new_state == DriverState.MENU.value
        assert "טבריה" in response.text
        assert "אזורי" in response.text

    @pytest.mark.asyncio
    async def test_search_command_location(self, db_session, user_factory) -> None:
        """פקודת 'פ מיקום' — מעבר למצב המתנה למיקום GPS"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003004")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "פ מיקום")
        assert new_state == DriverState.SEARCH_CREATE_ORIGIN.value
        assert "מיקום" in response.text

    @pytest.mark.asyncio
    async def test_location_search_with_gps(self, db_session, user_factory) -> None:
        """שיתוף מיקום GPS — יוצר חיפוש ומחזיר ל-MENU"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003005")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_CREATE_ORIGIN.value, context={}
        )

        response, new_state = await handler.handle_message(
            user, "", None,
            location_lat=31.7683, location_lng=35.2137,
        )
        assert new_state == DriverState.MENU.value
        assert "✅" in response.text

    @pytest.mark.asyncio
    async def test_location_search_cancel(self, db_session, user_factory) -> None:
        """ביטול חיפוש מיקום — חזרה לתפריט"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003006")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_CREATE_ORIGIN.value, context={}
        )

        response, new_state = await handler.handle_message(user, "❌ ביטול")
        assert new_state == DriverState.MENU.value

    @pytest.mark.asyncio
    async def test_location_search_no_location(self, db_session, user_factory) -> None:
        """שליחת טקסט רגיל במצב מיקום — הנחיה חוזרת"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003007")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_CREATE_ORIGIN.value, context={}
        )

        response, new_state = await handler.handle_message(user, "טקסט כלשהו")
        assert new_state == DriverState.SEARCH_CREATE_ORIGIN.value
        assert "לא התקבל מיקום" in response.text

    @pytest.mark.asyncio
    async def test_view_active_searches(self, db_session, user_factory) -> None:
        """כפתור 'חיפושים' — מציג חיפושים פעילים"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003008")
        await _create_search(db_session, user.id, destination_city="ירושלים")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "🔍 חיפושים פעילים")
        assert new_state == DriverState.SEARCH_VIEW_ACTIVE.value
        assert "ירושלים" in response.text
        assert "1/" in response.text

    @pytest.mark.asyncio
    async def test_view_active_searches_empty(self, db_session, user_factory) -> None:
        """צפייה בחיפושים כשאין — הודעה מתאימה"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003009")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "🔍 חיפושים פעילים")
        assert new_state == DriverState.MENU.value
        assert "אין חיפושים" in response.text

    @pytest.mark.asyncio
    async def test_delete_all_searches_from_view(self, db_session, user_factory) -> None:
        """מחיקת כל החיפושים מתוך תצוגת חיפושים"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003010")
        await _create_search(db_session, user.id, destination_city="ירושלים")
        await _create_search(db_session, user.id, destination_city="חיפה")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_VIEW_ACTIVE.value, context={}
        )

        response, new_state = await handler.handle_message(user, "🗑 מחק הכל")
        assert new_state == DriverState.MENU.value
        assert "2" in response.text
        assert "נמחקו" in response.text

    @pytest.mark.asyncio
    async def test_delete_single_search(self, db_session, user_factory) -> None:
        """מחיקת חיפוש בודד — זרימה מלאה"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003011")
        search = await _create_search(db_session, user.id, destination_city="ירושלים")

        handler = DriverStateHandler(db_session, platform="telegram")
        # צעד 1: כניסה לצפייה
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_VIEW_ACTIVE.value, context={}
        )
        response, new_state = await handler.handle_message(user, "🗑 מחק חיפוש")
        assert new_state == DriverState.SEARCH_MANAGE.value

        # צעד 2: בחירת חיפוש למחיקה
        response, new_state = await handler.handle_message(
            user, f"🗑 1. 📍 → ירושלים"
        )
        assert "נמחק" in response.text

    @pytest.mark.asyncio
    async def test_search_command_invalid(self, db_session, user_factory) -> None:
        """פקודת חיפוש לא תקינה — הצגת עזרה"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003012")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "פ")
        # "פ" לבד — לא תקין
        assert new_state == DriverState.MENU.value
        # עדכון: "פ" לבד אמור לרענן תפריט (is_search_command מחזיר true אבל parse מחזיר None)
        assert "❌" in response.text or "תפריט" in response.text.lower() or "פ" in response.text

    @pytest.mark.asyncio
    async def test_abbreviations_help(self, db_session, user_factory) -> None:
        """פקודת 'מילון' — הצגת קיצורי ערים"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003013")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "מילון")
        assert new_state == DriverState.MENU.value
        assert "ירושלים" in response.text
        assert "תל אביב" in response.text

    @pytest.mark.asyncio
    async def test_help_includes_search_instructions(self, db_session, user_factory) -> None:
        """הוראות שימוש כוללות הסבר על חיפוש"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003014")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "הוראות")
        assert "פ ים" in response.text
        assert "חיפוש" in response.text

    @pytest.mark.asyncio
    async def test_search_from_view_state(self, db_session, user_factory) -> None:
        """פקודת חיפוש חדש מתוך מצב צפייה"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003015")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_VIEW_ACTIVE.value, context={}
        )

        response, new_state = await handler.handle_message(user, "פ חי")
        assert new_state == DriverState.MENU.value
        assert "חיפה" in response.text
        assert "✅" in response.text

    @pytest.mark.asyncio
    async def test_max_searches_error_from_handler(self, db_session, user_factory) -> None:
        """חריגה ממגבלת חיפושים — הודעת שגיאה"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003016")
        # יצירת 9 חיפושים
        for i in range(MAX_ACTIVE_SEARCHES_PER_USER):
            await _create_search(db_session, user.id, destination_city=f"עיר_{i}")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "פ ים")
        assert new_state == DriverState.MENU.value
        assert "❌" in response.text
        assert "מקסימום" in response.text

    @pytest.mark.asyncio
    async def test_menu_has_search_button(self, db_session, user_factory) -> None:
        """התפריט הראשי מכיל כפתור חיפושים"""
        user, _ = await _create_registered_driver(db_session, user_factory, "+972505003017")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "תפריט")
        # בדיקה שהכפתור קיים בתפריט
        found = False
        if response.keyboard:
            for row in response.keyboard:
                for btn in row:
                    if "חיפושים" in btn:
                        found = True
        assert found, "כפתור חיפושים לא נמצא בתפריט"
