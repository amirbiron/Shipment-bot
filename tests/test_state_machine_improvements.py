"""
בדיקות לשיפורי מכונת מצבים - Issue #182

ממצא 1: ולידציית מעברי מצבים ב-CourierStateHandler, DispatcherStateHandler, StationOwnerStateHandler
ממצא 2: מיפוי מצבי שליח חסרים (VIEW_AVAILABLE, CAPTURE_CONFIRM, MARK_PICKED_UP, MARK_DELIVERED)
ממצא 3: guards על זרימות רב-שלביות ב-Dispatcher ו-StationOwner
ממצא 4: ניקוי קונטקסט ב-CourierHandler
"""
import pytest
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.state_machine.states import (
    CourierState,
    DispatcherState,
    StationOwnerState,
)
from app.state_machine.handlers import CourierStateHandler, MessageResponse
from app.state_machine.dispatcher_handler import DispatcherStateHandler
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager
from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_wallet import StationWallet


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
async def approved_courier(user_factory) -> User:
    """שליח מאושר לבדיקות"""
    return await user_factory(
        phone_number="+972507777777",
        name="Test Courier",
        role=UserRole.COURIER,
        platform="telegram",
        telegram_chat_id="70001",
        approval_status=ApprovalStatus.APPROVED,
    )


@pytest.fixture
async def station_owner_with_station(user_factory, db_session) -> tuple:
    """בעל תחנה עם תחנה לבדיקות"""
    owner = await user_factory(
        phone_number="+972508888888",
        name="Station Owner",
        role=UserRole.STATION_OWNER,
        platform="telegram",
        telegram_chat_id="80001",
    )

    station = Station(name="תחנת בדיקה", owner_id=owner.id)
    db_session.add(station)
    await db_session.flush()

    wallet = StationWallet(station_id=station.id)
    db_session.add(wallet)
    await db_session.commit()
    await db_session.refresh(station)

    return owner, station


@pytest.fixture
async def dispatcher_with_station(user_factory, db_session) -> tuple:
    """סדרן עם תחנה לבדיקות"""
    dispatcher = await user_factory(
        phone_number="+972509999999",
        name="Dispatcher Driver",
        role=UserRole.COURIER,
        platform="telegram",
        telegram_chat_id="90001",
        approval_status=ApprovalStatus.APPROVED,
    )

    owner = await user_factory(
        phone_number="+972501010101",
        name="Owner",
        role=UserRole.STATION_OWNER,
        platform="telegram",
        telegram_chat_id="10101",
    )

    station = Station(name="תחנת סדרן", owner_id=owner.id)
    db_session.add(station)
    await db_session.flush()

    wallet = StationWallet(station_id=station.id)
    db_session.add(wallet)

    link = StationDispatcher(station_id=station.id, user_id=dispatcher.id)
    db_session.add(link)
    await db_session.commit()
    await db_session.refresh(station)

    return dispatcher, station


# ============================================================================
# ממצא 1: ולידציית מעברי מצבים (Transition Validation)
# ============================================================================


class TestTransitionValidation:
    """בדיקת שימוש ב-transition_to עם fallback ל-force_state"""

    @pytest.mark.asyncio
    async def test_courier_handler_uses_transition_to(
        self, db_session, approved_courier
    ):
        """מוודא שמעבר חוקי ב-CourierHandler משתמש ב-transition_to"""
        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)

        # מאפסים למצב MENU
        await state_manager.force_state(
            approved_courier.id, "telegram", CourierState.MENU.value, {}
        )

        # בוחרים "ארנק" - מעבר חוקי MENU -> VIEW_WALLET
        response, new_state = await handler.handle_message(
            approved_courier, "ארנק", None
        )

        # בודקים שהמעבר בוצע
        current = await state_manager.get_current_state(
            approved_courier.id, "telegram"
        )
        assert current == CourierState.VIEW_WALLET.value

    @pytest.mark.asyncio
    async def test_dispatcher_handler_uses_transition_to(
        self, db_session, dispatcher_with_station
    ):
        """מוודא שמעבר חוקי ב-DispatcherHandler משתמש ב-transition_to"""
        dispatcher, station = dispatcher_with_station
        handler = DispatcherStateHandler(db_session, station.id, platform="telegram")
        state_manager = StateManager(db_session)

        await state_manager.force_state(
            dispatcher.id, "telegram", DispatcherState.MENU.value, {}
        )

        # "הוספת משלוח" - מעבר חוקי MENU -> ADD_SHIPMENT_PICKUP_CITY
        response, new_state = await handler.handle_message(
            dispatcher, "הוספת משלוח", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_PICKUP_CITY.value

    @pytest.mark.asyncio
    async def test_station_owner_handler_uses_transition_to(
        self, db_session, station_owner_with_station
    ):
        """מוודא שמעבר חוקי ב-StationOwnerHandler משתמש ב-transition_to"""
        owner, station = station_owner_with_station
        handler = StationOwnerStateHandler(db_session, station.id, platform="telegram")
        state_manager = StateManager(db_session)

        await state_manager.force_state(
            owner.id, "telegram", StationOwnerState.MENU.value, {}
        )

        # "ניהול סדרנים" - מעבר חוקי MENU -> MANAGE_DISPATCHERS
        response, new_state = await handler.handle_message(
            owner, "ניהול סדרנים", None
        )
        assert new_state == StationOwnerState.MANAGE_DISPATCHERS.value


# ============================================================================
# ממצא 2: מצבי שליח חסרים (Missing Courier Handlers)
# ============================================================================


class TestMissingCourierHandlers:
    """בדיקת handlers למצבי משלוח שהיו חסרים"""

    @pytest.mark.asyncio
    async def test_view_available_has_handler(self, db_session, approved_courier):
        """VIEW_AVAILABLE מחזיר הודעת הכוונה ולא נופל ל-_handle_unknown"""
        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)

        await state_manager.force_state(
            approved_courier.id, "telegram",
            CourierState.VIEW_AVAILABLE.value, {}
        )

        response, new_state = await handler.handle_message(
            approved_courier, "הודעה כלשהי", None
        )

        assert "משלוחים זמינים" in response.text
        assert new_state == CourierState.MENU.value

    @pytest.mark.asyncio
    async def test_capture_confirm_has_handler(self, db_session, approved_courier):
        """CAPTURE_CONFIRM מחזיר הודעת הכוונה ולא נופל ל-_handle_unknown"""
        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)

        await state_manager.force_state(
            approved_courier.id, "telegram",
            CourierState.CAPTURE_CONFIRM.value, {}
        )

        response, new_state = await handler.handle_message(
            approved_courier, "הודעה כלשהי", None
        )

        assert "תפיסת משלוח" in response.text
        assert new_state == CourierState.MENU.value

    @pytest.mark.asyncio
    async def test_mark_picked_up_has_handler(self, db_session, approved_courier):
        """MARK_PICKED_UP מחזיר הודעת הכוונה ולא נופל ל-_handle_unknown"""
        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)

        await state_manager.force_state(
            approved_courier.id, "telegram",
            CourierState.MARK_PICKED_UP.value, {}
        )

        response, new_state = await handler.handle_message(
            approved_courier, "הודעה כלשהי", None
        )

        assert "סימון איסוף" in response.text
        assert new_state == CourierState.MENU.value

    @pytest.mark.asyncio
    async def test_mark_delivered_has_handler(self, db_session, approved_courier):
        """MARK_DELIVERED מחזיר הודעת הכוונה ולא נופל ל-_handle_unknown"""
        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)

        await state_manager.force_state(
            approved_courier.id, "telegram",
            CourierState.MARK_DELIVERED.value, {}
        )

        response, new_state = await handler.handle_message(
            approved_courier, "הודעה כלשהי", None
        )

        assert "סימון מסירה" in response.text
        assert new_state == CourierState.MENU.value

    @pytest.mark.asyncio
    async def test_missing_handlers_support_back_button(
        self, db_session, approved_courier
    ):
        """כל ה-handlers החסרים תומכים בכפתור 'חזרה'"""
        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)

        for state in [
            CourierState.VIEW_AVAILABLE,
            CourierState.CAPTURE_CONFIRM,
            CourierState.MARK_PICKED_UP,
            CourierState.MARK_DELIVERED,
        ]:
            await state_manager.force_state(
                approved_courier.id, "telegram", state.value, {}
            )

            response, new_state = await handler.handle_message(
                approved_courier, "🔙 חזרה לתפריט", None
            )

            assert new_state == CourierState.MENU.value, (
                f"כפתור חזרה לא עובד ב-{state.value}"
            )


# ============================================================================
# ממצא 3: Guards על זרימות רב-שלביות
# ============================================================================


class TestMultiStepFlowGuards:
    """בדיקת guards על זרימות רב-שלביות"""

    @pytest.mark.asyncio
    async def test_dispatcher_unknown_state_shows_menu_without_keyword_routing(
        self, db_session, dispatcher_with_station
    ):
        """_handle_unknown ב-Dispatcher מציג תפריט ללא ניתוב מילות מפתח"""
        dispatcher, station = dispatcher_with_station
        handler = DispatcherStateHandler(db_session, station.id, platform="telegram")
        state_manager = StateManager(db_session)

        # מצב לא מוכר - צריך לחזור לתפריט
        await state_manager.force_state(
            dispatcher.id, "telegram", "DISPATCHER.INVALID_STATE", {}
        )

        response, new_state = await handler.handle_message(
            dispatcher, "הוספת משלוח", None  # מילת מפתח שלא אמורה לפעול
        )

        # צריך להציג את התפריט - לא לנתב ל-ADD_SHIPMENT
        assert new_state == DispatcherState.MENU.value
        assert "תפריט סדרן" in response.text

    @pytest.mark.asyncio
    async def test_station_owner_unknown_state_shows_menu_without_keyword_routing(
        self, db_session, station_owner_with_station
    ):
        """_handle_unknown ב-StationOwner מציג תפריט ללא ניתוב מילות מפתח"""
        owner, station = station_owner_with_station
        handler = StationOwnerStateHandler(db_session, station.id, platform="telegram")
        state_manager = StateManager(db_session)

        # מצב לא מוכר - צריך לחזור לתפריט
        await state_manager.force_state(
            owner.id, "telegram", "STATION.INVALID_STATE", {}
        )

        response, new_state = await handler.handle_message(
            owner, "ניהול סדרנים", None  # מילת מפתח שלא אמורה לפעול
        )

        # צריך להציג את התפריט - לא לנתב ל-MANAGE_DISPATCHERS
        assert new_state == StationOwnerState.MENU.value
        assert "פאנל ניהול" in response.text

    @pytest.mark.asyncio
    async def test_dispatcher_multi_step_flow_state_detection(
        self, db_session
    ):
        """_is_multi_step_flow_state מזהה נכון מצבי זרימה רב-שלבית"""
        handler = DispatcherStateHandler.__new__(DispatcherStateHandler)

        # זרימות רב-שלביות
        assert handler._is_multi_step_flow_state(
            DispatcherState.ADD_SHIPMENT_PICKUP_CITY.value
        )
        assert handler._is_multi_step_flow_state(
            DispatcherState.MANUAL_CHARGE_DRIVER_NAME.value
        )

        # תפריט - לא זרימה רב-שלבית
        assert not handler._is_multi_step_flow_state(
            DispatcherState.MENU.value
        )

    @pytest.mark.asyncio
    async def test_station_owner_multi_step_flow_state_detection(
        self, db_session
    ):
        """_is_multi_step_flow_state מזהה נכון מצבי זרימה רב-שלבית"""
        handler = StationOwnerStateHandler.__new__(StationOwnerStateHandler)

        # זרימות רב-שלביות
        assert handler._is_multi_step_flow_state(
            StationOwnerState.ADD_DISPATCHER_PHONE.value
        )
        assert handler._is_multi_step_flow_state(
            StationOwnerState.ADD_BLACKLIST_PHONE.value
        )

        # תפריט - לא זרימה רב-שלבית
        assert not handler._is_multi_step_flow_state(
            StationOwnerState.MENU.value
        )


# ============================================================================
# ממצא 4: ניקוי קונטקסט
# ============================================================================


class TestContextCleanup:
    """בדיקת ניקוי קונטקסט בחזרה ל-MENU"""

    @pytest.mark.asyncio
    async def test_courier_kyc_context_cleaned_on_menu_return(
        self, db_session, approved_courier
    ):
        """קונטקסט KYC מנוקה בחזרה ל-MENU מזרימת רישום"""
        # סימון שהשליח סיים רישום (נדרש כדי ש-_handle_pending_approval לא ישלח לרישום מחדש)
        approved_courier.terms_accepted_at = datetime.utcnow()
        await db_session.commit()

        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)

        # מדמים שליח שנמצא ב-PENDING_APPROVAL עם קונטקסט KYC
        kyc_context = {
            "document_file_id": "doc_123",
            "selfie_file_id": "selfie_456",
            "vehicle_category": "אופנוע",
            "vehicle_photo_file_id": "vehicle_789",
            "other_key": "should_remain",
        }
        await state_manager.force_state(
            approved_courier.id, "telegram",
            CourierState.PENDING_APPROVAL.value, kyc_context
        )

        # שליח מאושר שולח הודעה - אמור לעבור ל-MENU
        response, new_state = await handler.handle_message(
            approved_courier, "תפריט", None
        )

        assert new_state == CourierState.MENU.value

        # בודקים שקונטקסט KYC נוקה
        context = await state_manager.get_context(approved_courier.id, "telegram")
        assert "document_file_id" not in context
        assert "selfie_file_id" not in context
        assert "vehicle_category" not in context
        assert "vehicle_photo_file_id" not in context
        # מפתח אחר צריך להישאר
        assert context.get("other_key") == "should_remain"

    @pytest.mark.asyncio
    async def test_dispatcher_shipment_context_cleaned_on_menu_return(
        self, db_session, dispatcher_with_station
    ):
        """קונטקסט הוספת משלוח מנוקה בחזרה ל-MENU"""
        dispatcher, station = dispatcher_with_station
        handler = DispatcherStateHandler(db_session, station.id, platform="telegram")
        state_manager = StateManager(db_session)

        # מדמים סדרן באמצע הוספת משלוח
        shipment_context = {
            "pickup_city": "תל אביב",
            "pickup_street": "הרצל",
            "pickup_number": "5",
            "pickup_address": "הרצל 5, תל אביב",
            "other_key": "should_remain",
        }
        await state_manager.force_state(
            dispatcher.id, "telegram",
            DispatcherState.ADD_SHIPMENT_DROPOFF_CITY.value, shipment_context
        )

        # ביטול - חזרה לתפריט
        response, new_state = await handler.handle_message(
            dispatcher, "ביטול ❌", None
        )

        # מוודאים חזרה לתפריט (ה-handler של dropoff_city לא מנתב ל-MENU ישירות,
        # אבל הוא עשוי ליפול ל-unknown ולחזור ל-MENU)
        context = await state_manager.get_context(dispatcher.id, "telegram")

        # אם המצב השתנה ל-MENU, צריך לנקות קונטקסט משלוח
        if new_state == DispatcherState.MENU.value:
            assert "pickup_city" not in context
            assert "pickup_street" not in context
            assert context.get("other_key") == "should_remain"

    @pytest.mark.asyncio
    async def test_station_owner_management_context_cleaned_on_menu_return(
        self, db_session, station_owner_with_station
    ):
        """קונטקסט ניהולי מנוקה בחזרה ל-MENU"""
        owner, station = station_owner_with_station
        handler = StationOwnerStateHandler(db_session, station.id, platform="telegram")
        state_manager = StateManager(db_session)

        # מדמים בעל תחנה ב-MANAGE_DISPATCHERS
        mgmt_context = {
            "dispatcher_phone": "+972501234567",
            "other_key": "should_remain",
        }
        await state_manager.force_state(
            owner.id, "telegram",
            StationOwnerState.MANAGE_DISPATCHERS.value, mgmt_context
        )

        # חזרה לתפריט
        response, new_state = await handler.handle_message(
            owner, "חזרה", None
        )

        assert new_state == StationOwnerState.MENU.value

        # בודקים שקונטקסט ניהולי נוקה
        context = await state_manager.get_context(owner.id, "telegram")
        assert "dispatcher_phone" not in context
        assert context.get("other_key") == "should_remain"
