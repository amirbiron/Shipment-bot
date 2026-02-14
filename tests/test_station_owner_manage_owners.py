"""
בדיקות ניהול בעלים דרך הבוט (state machine handler)
"""
import pytest
from sqlalchemy import select

from app.db.models.user import User, UserRole
from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_wallet import StationWallet
from app.state_machine.states import StationOwnerState
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager
from app.domain.services.station_service import StationService


class TestOwnerManagementHandler:
    """בדיקות זרימת ניהול בעלים דרך הבוט"""

    async def _setup_station(self, user_factory, db_session):
        """הקמת תחנה עם בעלים אחד"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעלים ראשון",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
        await db_session.commit()
        return owner, station

    async def _setup_station_with_two_owners(self, user_factory, db_session):
        """הקמת תחנה עם שני בעלים"""
        owner1 = await user_factory(
            phone_number="+972501234567",
            name="בעלים ראשון",
            role=UserRole.STATION_OWNER,
        )
        owner2 = await user_factory(
            phone_number="+972502222222",
            name="בעלים שני",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=owner1.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner1.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner2.id))
        await db_session.commit()
        return owner1, owner2, station

    @pytest.mark.asyncio
    async def test_menu_shows_manage_owners_button(self, user_factory, db_session):
        """תפריט ראשי מציג כפתור ניהול בעלים"""
        owner, station = await self._setup_station(user_factory, db_session)
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(owner.id, "telegram", StationOwnerState.MENU.value, {})

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "תפריט", None)

        assert "ניהול בעלים" in response.text or any(
            "ניהול בעלים" in str(row) for row in (response.keyboard or [])
        )

    @pytest.mark.asyncio
    async def test_navigate_to_manage_owners(self, user_factory, db_session):
        """ניווט לניהול בעלים מתפריט ראשי"""
        owner, station = await self._setup_station(user_factory, db_session)
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(owner.id, "telegram", StationOwnerState.MENU.value, {})

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "👤 ניהול בעלים", None)

        assert new_state == StationOwnerState.MANAGE_OWNERS.value
        assert "ניהול בעלים" in response.text

    @pytest.mark.asyncio
    async def test_manage_owners_shows_current_owners(self, user_factory, db_session):
        """מסך ניהול בעלים מציג רשימת בעלים"""
        owner, station = await self._setup_station(user_factory, db_session)
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(owner.id, "telegram", StationOwnerState.MENU.value, {})

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "בעלים", None)

        assert "בעלים ראשון" in response.text
        assert "(אתה)" in response.text

    @pytest.mark.asyncio
    async def test_add_owner_prompt(self, user_factory, db_session):
        """לחיצה על הוספת בעלים מציגה בקשה למספר טלפון"""
        owner, station = await self._setup_station(user_factory, db_session)
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.MANAGE_OWNERS.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "➕ הוספת בעלים", None)

        assert new_state == StationOwnerState.ADD_OWNER_PHONE.value
        assert "מספר הטלפון" in response.text

    @pytest.mark.asyncio
    async def test_add_owner_success(self, user_factory, db_session):
        """הוספת בעלים מוצלחת"""
        owner, station = await self._setup_station(user_factory, db_session)
        await user_factory(
            phone_number="+972502222222",
            name="בעלים חדש",
            role=UserRole.SENDER,
        )
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.ADD_OWNER_PHONE.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "0502222222", None)

        assert new_state == StationOwnerState.MANAGE_OWNERS.value
        assert "נוסף בהצלחה" in response.text

        # ולידציה שהבעלים נוסף
        service = StationService(db_session)
        assert await service.is_owner_of_station(
            (await db_session.execute(
                select(User).where(User.phone_number == "+972502222222")
            )).scalar_one().id,
            station.id
        ) is True

    @pytest.mark.asyncio
    async def test_add_owner_invalid_phone(self, user_factory, db_session):
        """הוספת בעלים עם מספר לא תקין"""
        owner, station = await self._setup_station(user_factory, db_session)
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.ADD_OWNER_PHONE.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "abc", None)

        assert new_state == StationOwnerState.MANAGE_OWNERS.value
        assert "לא תקין" in response.text

    @pytest.mark.asyncio
    async def test_add_owner_duplicate(self, user_factory, db_session):
        """הוספת בעלים שכבר קיים"""
        owner, station = await self._setup_station(user_factory, db_session)
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.ADD_OWNER_PHONE.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "0501234567", None)

        assert new_state == StationOwnerState.MANAGE_OWNERS.value
        assert "כבר בעלים" in response.text

    @pytest.mark.asyncio
    async def test_add_owner_back_button(self, user_factory, db_session):
        """חזרה מהוספת בעלים"""
        owner, station = await self._setup_station(user_factory, db_session)
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.ADD_OWNER_PHONE.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "חזרה", None)

        assert new_state == StationOwnerState.MANAGE_OWNERS.value

    @pytest.mark.asyncio
    async def test_remove_owner_shows_list(self, user_factory, db_session):
        """הצגת רשימת בעלים להסרה"""
        owner1, owner2, station = await self._setup_station_with_two_owners(
            user_factory, db_session
        )
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner1.id, "telegram", StationOwnerState.MANAGE_OWNERS.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner1, "➖ הסרת בעלים", None)

        assert new_state == StationOwnerState.REMOVE_OWNER_SELECT.value
        assert "בעלים ראשון" in response.text
        assert "בעלים שני" in response.text

    @pytest.mark.asyncio
    async def test_remove_owner_single_owner_blocked(self, user_factory, db_session):
        """לא ניתן להסיר בעלים כשיש רק אחד"""
        owner, station = await self._setup_station(user_factory, db_session)
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.MANAGE_OWNERS.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "➖ הסרת בעלים", None)

        assert new_state == StationOwnerState.MANAGE_OWNERS.value
        assert "לפחות בעלים אחד" in response.text

    @pytest.mark.asyncio
    async def test_remove_owner_select_and_confirm(self, user_factory, db_session):
        """בחירת בעלים להסרה ואישור"""
        owner1, owner2, station = await self._setup_station_with_two_owners(
            user_factory, db_session
        )
        state_mgr = StateManager(db_session)

        # שלב 1: ניווט להסרה
        await state_mgr.force_state(
            owner1.id, "telegram", StationOwnerState.MANAGE_OWNERS.value, {}
        )
        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner1, "הסרת בעלים", None)
        assert new_state == StationOwnerState.REMOVE_OWNER_SELECT.value

        # שלב 2: בחירת בעלים (מספר 2)
        response, new_state = await handler.handle_message(owner1, "הסר 2", None)
        assert new_state == StationOwnerState.CONFIRM_REMOVE_OWNER.value
        assert "בעלים שני" in response.text
        assert "בטוח" in response.text

        # שלב 3: אישור
        response, new_state = await handler.handle_message(owner1, "✅ כן, הסר", None)
        assert new_state == StationOwnerState.MANAGE_OWNERS.value
        assert "הוסר בהצלחה" in response.text

        # ולידציה שהבעלים הוסר
        service = StationService(db_session)
        assert await service.is_owner_of_station(owner2.id, station.id) is False

    @pytest.mark.asyncio
    async def test_remove_owner_cancel(self, user_factory, db_session):
        """ביטול הסרת בעלים"""
        owner1, owner2, station = await self._setup_station_with_two_owners(
            user_factory, db_session
        )
        state_mgr = StateManager(db_session)

        # ניווט לאישור הסרה
        await state_mgr.force_state(
            owner1.id, "telegram", StationOwnerState.CONFIRM_REMOVE_OWNER.value,
            {"remove_owner_id": owner2.id, "remove_owner_name": "בעלים שני"}
        )
        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner1, "❌ ביטול", None)

        assert new_state == StationOwnerState.MANAGE_OWNERS.value

        # הבעלים עדיין קיים
        service = StationService(db_session)
        assert await service.is_owner_of_station(owner2.id, station.id) is True

    @pytest.mark.asyncio
    async def test_remove_owner_invalid_selection(self, user_factory, db_session):
        """בחירה לא תקינה בהסרת בעלים"""
        owner1, owner2, station = await self._setup_station_with_two_owners(
            user_factory, db_session
        )
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner1.id, "telegram", StationOwnerState.REMOVE_OWNER_SELECT.value,
            {"owner_map": {"1": owner1.id, "2": owner2.id}}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner1, "הסר 99", None)

        assert new_state == StationOwnerState.REMOVE_OWNER_SELECT.value
        assert "לא תקינה" in response.text

    @pytest.mark.asyncio
    async def test_manage_owners_back_to_menu(self, user_factory, db_session):
        """חזרה לתפריט ראשי מניהול בעלים"""
        owner, station = await self._setup_station(user_factory, db_session)
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.MANAGE_OWNERS.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "🔙 חזרה לתפריט", None)

        assert new_state == StationOwnerState.MENU.value


class TestOwnerManagementGetOwners:
    """בדיקות get_owners ב-StationService"""

    @pytest.mark.asyncio
    async def test_get_owners_returns_active_only(self, user_factory, db_session):
        """get_owners מחזיר רק בעלים פעילים"""
        owner1 = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        owner2 = await user_factory(
            phone_number="+972502222222",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=owner1.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner1.id, is_active=True))
        db_session.add(StationOwner(station_id=station.id, user_id=owner2.id, is_active=False))
        await db_session.commit()

        service = StationService(db_session)
        owners = await service.get_owners(station.id)

        assert len(owners) == 1
        assert owners[0].user_id == owner1.id

    @pytest.mark.asyncio
    async def test_get_owners_legacy_fallback(self, user_factory, db_session):
        """get_owners מחזיר בעלים מ-owner_id גם בלי רשומה ב-junction (תאימות לאחור)"""
        owner = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        # לא מוסיפים StationOwner — מדמה תחנה לפני מיגרציה
        await db_session.commit()

        service = StationService(db_session)
        owners = await service.get_owners(station.id)

        # fallback ל-owner_id — מחזיר בעלים אחד
        assert len(owners) == 1
        assert owners[0].user_id == owner.id
