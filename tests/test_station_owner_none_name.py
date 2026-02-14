"""
בדיקות: תצוגת שמות None בניהול סדרנים ורשימה שחורה

מוודא שכאשר dispatcher/courier נוצרים דרך get_or_create_user_by_phone
(בלי שם), ה-handler לא קורס ומציג "לא ידוע" במקום.
"""
import pytest
from sqlalchemy import select

from app.db.models.user import User, UserRole
from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_blacklist import StationBlacklist
from app.db.models.station_wallet import StationWallet
from app.state_machine.states import StationOwnerState
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager


class TestDispatcherNoneName:
    """בדיקות: סדרנים עם name=None לא גורמים לקריסה"""

    async def _setup_station_with_nameless_dispatcher(
        self, user_factory, db_session
    ):
        """הקמת תחנה עם סדרן ללא שם (סימולציה של get_or_create_user_by_phone)"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        # סדרן שנוצר דרך get_or_create_user_by_phone — בלי שם
        dispatcher = await user_factory(
            phone_number="+972509999999",
            name=None,
            full_name=None,
            role=UserRole.SENDER,
        )
        station = Station(name="תחנת בדיקה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
        db_session.add(
            StationDispatcher(station_id=station.id, user_id=dispatcher.id)
        )
        await db_session.commit()
        return owner, dispatcher, station

    @pytest.mark.asyncio
    async def test_manage_dispatchers_with_none_name(
        self, user_factory, db_session
    ):
        """תצוגת סדרנים לא קורסת כשלסדרן אין שם"""
        owner, dispatcher, station = (
            await self._setup_station_with_nameless_dispatcher(
                user_factory, db_session
            )
        )
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.MENU.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(
            owner, "👥 ניהול סדרנים", None
        )

        assert new_state == StationOwnerState.MANAGE_DISPATCHERS.value
        # "לא ידוע" במקום קריסה על escape(None)
        assert "לא ידוע" in response.text

    @pytest.mark.asyncio
    async def test_dispatcher_removal_list_with_none_name(
        self, user_factory, db_session
    ):
        """רשימת סדרנים להסרה לא קורסת כשלסדרן אין שם"""
        owner, dispatcher, station = (
            await self._setup_station_with_nameless_dispatcher(
                user_factory, db_session
            )
        )
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id,
            "telegram",
            StationOwnerState.MANAGE_DISPATCHERS.value,
            {},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(
            owner, "➖ הסרת סדרן", None
        )

        assert new_state == StationOwnerState.REMOVE_DISPATCHER_SELECT.value
        assert "לא ידוע" in response.text

    @pytest.mark.asyncio
    async def test_dispatcher_confirm_remove_with_none_name(
        self, user_factory, db_session
    ):
        """אישור הסרת סדרן ללא שם לא קורס"""
        owner, dispatcher, station = (
            await self._setup_station_with_nameless_dispatcher(
                user_factory, db_session
            )
        )
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id,
            "telegram",
            StationOwnerState.REMOVE_DISPATCHER_SELECT.value,
            {"dispatcher_map": {"1": dispatcher.id}},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(
            owner, "הסר 1", None
        )

        assert new_state == StationOwnerState.CONFIRM_REMOVE_DISPATCHER.value
        assert "לא ידוע" in response.text

    @pytest.mark.asyncio
    async def test_dispatcher_with_full_name_only(
        self, user_factory, db_session
    ):
        """סדרן עם full_name בלבד (בלי name) מציג full_name"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        dispatcher = await user_factory(
            phone_number="+972509999999",
            name=None,
            full_name="יוסי כהן",
            role=UserRole.SENDER,
        )
        station = Station(name="תחנת בדיקה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
        db_session.add(
            StationDispatcher(station_id=station.id, user_id=dispatcher.id)
        )
        await db_session.commit()

        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.MENU.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(
            owner, "👥 ניהול סדרנים", None
        )

        assert "יוסי כהן" in response.text


class TestBlacklistNoneName:
    """בדיקות: נהגים ברשימה שחורה עם name=None לא גורמים לקריסה"""

    async def _setup_station_with_nameless_blacklisted(
        self, user_factory, db_session
    ):
        """הקמת תחנה עם נהג חסום ללא שם"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        courier = await user_factory(
            phone_number="+972508888888",
            name=None,
            full_name=None,
            role=UserRole.COURIER,
        )
        station = Station(name="תחנת בדיקה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
        db_session.add(
            StationBlacklist(
                station_id=station.id,
                courier_id=courier.id,
                reason="אי תשלום",
            )
        )
        await db_session.commit()
        return owner, courier, station

    @pytest.mark.asyncio
    async def test_blacklist_view_with_none_name(
        self, user_factory, db_session
    ):
        """תצוגת רשימה שחורה לא קורסת כשלנהג אין שם"""
        owner, courier, station = (
            await self._setup_station_with_nameless_blacklisted(
                user_factory, db_session
            )
        )
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, "telegram", StationOwnerState.MENU.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(
            owner, "🚫 רשימה שחורה", None
        )

        assert new_state == StationOwnerState.VIEW_BLACKLIST.value
        assert "לא ידוע" in response.text

    @pytest.mark.asyncio
    async def test_blacklist_removal_list_with_none_name(
        self, user_factory, db_session
    ):
        """רשימת נהגים חסומים להסרה לא קורסת כשלנהג אין שם"""
        owner, courier, station = (
            await self._setup_station_with_nameless_blacklisted(
                user_factory, db_session
            )
        )
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id,
            "telegram",
            StationOwnerState.VIEW_BLACKLIST.value,
            {},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(
            owner, "➖ הסרת נהג מהרשימה", None
        )

        assert new_state == StationOwnerState.REMOVE_BLACKLIST_SELECT.value
        assert "לא ידוע" in response.text

    @pytest.mark.asyncio
    async def test_blacklist_confirm_remove_with_none_name(
        self, user_factory, db_session
    ):
        """אישור הסרה מרשימה שחורה ללא שם לא קורס"""
        owner, courier, station = (
            await self._setup_station_with_nameless_blacklisted(
                user_factory, db_session
            )
        )
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id,
            "telegram",
            StationOwnerState.REMOVE_BLACKLIST_SELECT.value,
            {"blacklist_map": {"1": courier.id}},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(
            owner, "הסר 1", None
        )

        assert new_state == StationOwnerState.CONFIRM_REMOVE_BLACKLIST.value
        assert "לא ידוע" in response.text
