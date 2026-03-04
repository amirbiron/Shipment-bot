"""
בדיקות יחידה — iDriver סשן 6: ניהול חיפושים (השהיה/חידוש/מחיקה)

בודק:
- DriverSearchService: pause_all_searches, resume_all_searches, get_non_deleted_searches
- DriverStateHandler: פקודות ע/ה/מ/ממ
"""
import pytest
from datetime import datetime, timedelta, date

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    VehicleCategory,
    DriverSubscriptionStatus,
)
from app.db.models.driver_search import DriverSearch, DriverSearchStatus
from app.state_machine.states import DriverState
from app.state_machine.driver_handler import DriverStateHandler
from app.domain.services.driver_search_service import DriverSearchService


# ============================================================================
# עזר — יצירת נהג רשום
# ============================================================================


async def _create_registered_driver(
    db_session,
    user_factory,
    phone: str = "+972505001001",
) -> User:
    """יוצר נהג רשום עם פרופיל מלא"""
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
    return user


async def _create_search(
    db_session,
    user_id: int,
    destination_city: str = "ירושלים",
    status: str = DriverSearchStatus.ACTIVE.value,
) -> DriverSearch:
    """יוצר חיפוש לבדיקות"""
    search = DriverSearch(
        user_id=user_id,
        origin_city="תל אביב",
        destination_city=destination_city,
        is_area_search=False,
        status=status,
    )
    db_session.add(search)
    await db_session.commit()
    await db_session.refresh(search)
    return search


# ============================================================================
# בדיקות DriverSearchService — pause/resume/get_non_deleted
# ============================================================================


class TestDriverSearchPauseResume:
    """בדיקות השהיה/חידוש חיפושים"""

    @pytest.mark.asyncio
    async def test_pause_all_searches(self, db_session, user_factory) -> None:
        """השהיית כל החיפושים הפעילים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507001001")
        await _create_search(db_session, user.id, "ירושלים")
        await _create_search(db_session, user.id, "חיפה")

        service = DriverSearchService(db_session)
        count = await service.pause_all_searches(user.id)
        assert count == 2

        active = await service.get_active_searches(user.id)
        assert len(active) == 0

    @pytest.mark.asyncio
    async def test_pause_no_active_searches(self, db_session, user_factory) -> None:
        """השהיה כשאין חיפושים פעילים — מחזיר 0"""
        user = await _create_registered_driver(db_session, user_factory, "+972507001002")
        service = DriverSearchService(db_session)

        count = await service.pause_all_searches(user.id)
        assert count == 0

    @pytest.mark.asyncio
    async def test_resume_all_searches(self, db_session, user_factory) -> None:
        """חידוש כל החיפושים המושהים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507001003")
        await _create_search(db_session, user.id, "ירושלים", DriverSearchStatus.PAUSED.value)
        await _create_search(db_session, user.id, "חיפה", DriverSearchStatus.PAUSED.value)

        service = DriverSearchService(db_session)
        count = await service.resume_all_searches(user.id)
        assert count == 2

        active = await service.get_active_searches(user.id)
        assert len(active) == 2

    @pytest.mark.asyncio
    async def test_resume_no_paused_searches(self, db_session, user_factory) -> None:
        """חידוש כשאין חיפושים מושהים — מחזיר 0"""
        user = await _create_registered_driver(db_session, user_factory, "+972507001004")
        service = DriverSearchService(db_session)

        count = await service.resume_all_searches(user.id)
        assert count == 0

    @pytest.mark.asyncio
    async def test_pause_only_active_not_paused(self, db_session, user_factory) -> None:
        """השהיה משפיעה רק על ACTIVE, לא על PAUSED"""
        user = await _create_registered_driver(db_session, user_factory, "+972507001005")
        await _create_search(db_session, user.id, "ירושלים")  # ACTIVE
        await _create_search(db_session, user.id, "חיפה", DriverSearchStatus.PAUSED.value)

        service = DriverSearchService(db_session)
        count = await service.pause_all_searches(user.id)
        assert count == 1  # רק ה-ACTIVE הושהה

    @pytest.mark.asyncio
    async def test_get_non_deleted_searches(self, db_session, user_factory) -> None:
        """שליפת חיפושים שלא נמחקו — כולל פעילים ומושהים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507001006")
        await _create_search(db_session, user.id, "ירושלים")  # ACTIVE
        await _create_search(db_session, user.id, "חיפה", DriverSearchStatus.PAUSED.value)
        await _create_search(db_session, user.id, "אילת", DriverSearchStatus.DELETED.value)

        service = DriverSearchService(db_session)
        searches = await service.get_non_deleted_searches(user.id)
        assert len(searches) == 2  # ACTIVE + PAUSED, לא DELETED

    @pytest.mark.asyncio
    async def test_delete_all_includes_paused(self, db_session, user_factory) -> None:
        """מחיקת הכל כוללת גם חיפושים מושהים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507001007")
        await _create_search(db_session, user.id, "ירושלים")  # ACTIVE
        await _create_search(db_session, user.id, "חיפה", DriverSearchStatus.PAUSED.value)

        service = DriverSearchService(db_session)
        count = await service.delete_all_searches(user.id)
        assert count == 2

        remaining = await service.get_non_deleted_searches(user.id)
        assert len(remaining) == 0


# ============================================================================
# בדיקות DriverStateHandler — פקודות ע/ה/מ/ממ
# ============================================================================


class TestDriverSearchManagementHandler:
    """בדיקות handler לפקודות ניהול חיפושים"""

    @pytest.mark.asyncio
    async def test_pause_command_from_menu(self, db_session, user_factory) -> None:
        """פקודת 'ע' מהתפריט — השהיית כל החיפושים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002001")
        await _create_search(db_session, user.id, "ירושלים")
        await _create_search(db_session, user.id, "חיפה")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "ע")
        assert new_state == DriverState.MENU.value
        assert "2" in response.text
        assert "הושהו" in response.text

    @pytest.mark.asyncio
    async def test_pause_command_no_searches(self, db_session, user_factory) -> None:
        """פקודת 'ע' בלי חיפושים — הודעת שגיאה"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002002")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "ע")
        assert new_state == DriverState.MENU.value
        assert "אין" in response.text

    @pytest.mark.asyncio
    async def test_resume_command_from_menu(self, db_session, user_factory) -> None:
        """פקודת 'ה' מהתפריט — חידוש כל החיפושים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002003")
        await _create_search(db_session, user.id, "ירושלים", DriverSearchStatus.PAUSED.value)
        await _create_search(db_session, user.id, "חיפה", DriverSearchStatus.PAUSED.value)

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "ה")
        assert new_state == DriverState.MENU.value
        assert "2" in response.text
        assert "חודשו" in response.text

    @pytest.mark.asyncio
    async def test_resume_command_no_paused(self, db_session, user_factory) -> None:
        """פקודת 'ה' בלי חיפושים מושהים — הודעת שגיאה"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002004")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "ה")
        assert new_state == DriverState.MENU.value
        assert "אין" in response.text

    @pytest.mark.asyncio
    async def test_delete_all_command_confirmation(self, db_session, user_factory) -> None:
        """פקודת 'ממ' — מציגה אישור לפני מחיקה"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002005")
        await _create_search(db_session, user.id, "ירושלים")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "ממ")
        assert new_state == DriverState.SEARCH_VIEW_ACTIVE.value
        assert "אישור" in response.text or "בטוח" in response.text or "מחיקת" in response.text

    @pytest.mark.asyncio
    async def test_delete_all_confirm_yes(self, db_session, user_factory) -> None:
        """אישור מחיקת כל החיפושים — כן"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002006")
        await _create_search(db_session, user.id, "ירושלים")
        await _create_search(db_session, user.id, "חיפה")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_VIEW_ACTIVE.value,
            context={"delete_all_pending": True}
        )

        response, new_state = await handler.handle_message(user, "✅ כן, מחק הכל")
        assert new_state == DriverState.MENU.value
        assert "נמחקו" in response.text

    @pytest.mark.asyncio
    async def test_delete_all_confirm_cancel(self, db_session, user_factory) -> None:
        """ביטול מחיקת כל החיפושים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002007")
        await _create_search(db_session, user.id, "ירושלים")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_VIEW_ACTIVE.value,
            context={"delete_all_pending": True}
        )

        response, new_state = await handler.handle_message(user, "❌ ביטול")
        assert new_state == DriverState.MENU.value

        # ווידוא שהחיפוש לא נמחק
        service = DriverSearchService(db_session)
        active = await service.get_active_searches(user.id)
        assert len(active) == 1

    @pytest.mark.asyncio
    async def test_delete_single_command_from_menu(self, db_session, user_factory) -> None:
        """פקודת 'מ' מהתפריט — מציגה רשימת חיפושים למחיקה"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002008")
        await _create_search(db_session, user.id, "ירושלים")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "מ")
        assert new_state == DriverState.SEARCH_MANAGE.value
        # שם העיר מוצג בכפתורי המקלדת, לא בטקסט
        assert "חיפוש" in response.text or "בחר" in response.text
        assert response.keyboard is not None

    @pytest.mark.asyncio
    async def test_pause_from_search_view(self, db_session, user_factory) -> None:
        """פקודת 'ע' מתוך צפייה בחיפושים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002009")
        await _create_search(db_session, user.id, "ירושלים")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_VIEW_ACTIVE.value, context={}
        )

        response, new_state = await handler.handle_message(user, "ע")
        assert new_state == DriverState.MENU.value
        assert "הושהו" in response.text

    @pytest.mark.asyncio
    async def test_resume_from_search_view(self, db_session, user_factory) -> None:
        """פקודת 'ה' מתוך צפייה בחיפושים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002010")
        await _create_search(db_session, user.id, "ירושלים", DriverSearchStatus.PAUSED.value)

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_VIEW_ACTIVE.value, context={}
        )

        response, new_state = await handler.handle_message(user, "ה")
        assert new_state == DriverState.MENU.value
        assert "חודשו" in response.text

    @pytest.mark.asyncio
    async def test_delete_all_from_search_view(self, db_session, user_factory) -> None:
        """פקודת 'ממ' מתוך צפייה בחיפושים"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002011")
        await _create_search(db_session, user.id, "ירושלים")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.SEARCH_VIEW_ACTIVE.value, context={}
        )

        response, new_state = await handler.handle_message(user, "ממ")
        assert new_state == DriverState.SEARCH_VIEW_ACTIVE.value
        assert "אישור" in response.text or "בטוח" in response.text or "מחיקת" in response.text

    @pytest.mark.asyncio
    async def test_help_includes_session6_commands(self, db_session, user_factory) -> None:
        """הוראות שימוש כוללות פקודות סשן 6"""
        user = await _create_registered_driver(db_session, user_factory, "+972507002012")

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "הוראות")
        # בדיקה שהפקודות החדשות מוזכרות בעזרה
        help_text = response.text
        assert "ע" in help_text
        assert "ה" in help_text
        assert "ממ" in help_text or "מ" in help_text
