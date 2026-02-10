"""
בדיקות לתיקוני Issue #144 — ריוויו תרשימי זרימה.

תיקון 1: ניקוי קונטקסט טיוטת משלוח בחזרה ל-SENDER_MENU מתוך handlers.
תיקון 2: שלבי אישור לפעולות הרסניות של בעל תחנה (הסרת סדרן / הסרה מרשימה שחורה).
תיקון 3: ניקוי rejection_note בהגשה מחדש של שליח (REJECTED → PENDING).
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.state_machine.states import (
    SenderState,
    CourierState,
    StationOwnerState,
    STATION_OWNER_TRANSITIONS,
)
from app.state_machine.handlers import SenderStateHandler, CourierStateHandler
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.manager import StateManager
from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_wallet import StationWallet
from app.db.models.station_blacklist import StationBlacklist
from app.domain.services.station_service import StationService
from app.domain.services.courier_approval_service import CourierApprovalService


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def station_factory(db_session: AsyncSession):
    """Factory ליצירת תחנה עם ארנק"""
    async def _create_station(
        name: str = "תחנת בדיקה",
        owner_id: int = None,
    ) -> Station:
        station = Station(name=name, owner_id=owner_id)
        db_session.add(station)
        await db_session.flush()

        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()
        await db_session.refresh(station)
        return station

    return _create_station


@pytest.fixture
async def station_with_owner_and_dispatcher(
    db_session, user_factory, station_factory
):
    """יצירת תחנה עם בעלים, סדרן, ונהג ברשימה שחורה"""
    owner = await user_factory(
        phone_number="+972503333333",
        name="Station Owner",
        role=UserRole.STATION_OWNER,
        platform="telegram",
        telegram_chat_id="30001",
    )

    dispatcher = await user_factory(
        phone_number="+972504444444",
        name="Dispatcher Driver",
        role=UserRole.COURIER,
        platform="telegram",
        telegram_chat_id="40001",
        approval_status=ApprovalStatus.APPROVED,
    )

    station = await station_factory(
        name="תחנת בדיקה",
        owner_id=owner.id,
    )

    dispatcher_link = StationDispatcher(
        station_id=station.id,
        user_id=dispatcher.id,
    )
    db_session.add(dispatcher_link)
    await db_session.commit()

    return station, owner, dispatcher


# ============================================================================
# תיקון 1: ניקוי קונטקסט טיוטת משלוח בחזרה ל-SENDER_MENU
# ============================================================================


class TestSenderContextCleanup:
    """בדיקות לניקוי קונטקסט משלוח בחזרה לתפריט"""

    @pytest.mark.asyncio
    async def test_context_cleared_on_delivery_confirm(self, db_session, user_factory):
        """אישור משלוח מנקה את נתוני הטיוטה מהקונטקסט"""
        user = await user_factory(
            phone_number="+972501111111",
            name="Sender",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="11111",
        )

        state_manager = StateManager(db_session)
        # מכניסים קונטקסט עם נתוני טיוטה
        delivery_context = {
            "name": "Sender",
            "pickup_city": "תל אביב",
            "pickup_street": "הרצל",
            "pickup_number": "10",
            "pickup_apartment": "",
            "pickup_address": "הרצל 10, תל אביב",
            "dropoff_city": "ירושלים",
            "dropoff_street": "יפו",
            "dropoff_number": "5",
            "dropoff_apartment": "",
            "dropoff_address": "יפו 5, ירושלים",
            "delivery_location": "outside_city",
            "urgency": "immediate",
            "delivery_time": "מיידי",
            "description": "חבילה",
        }
        await state_manager.force_state(
            user.id, "telegram",
            SenderState.DELIVERY_CONFIRM.value,
            delivery_context,
        )

        handler = SenderStateHandler(db_session)
        response, new_state = await handler.handle_message(
            user.id, "telegram", "✅ אישור ושליחה"
        )

        assert new_state == SenderState.MENU.value

        # בדיקה שהקונטקסט נוקה מנתוני המשלוח
        context = await state_manager.get_context(user.id, "telegram")
        assert "pickup_city" not in context
        assert "dropoff_address" not in context
        assert "delivery_location" not in context
        assert "urgency" not in context
        assert "description" not in context
        # שדות שאינם חלק מטיוטת משלוח צריכים להישאר
        assert context.get("name") == "Sender"

    @pytest.mark.asyncio
    async def test_context_cleared_on_delivery_cancel(self, db_session, user_factory):
        """ביטול משלוח מנקה את נתוני הטיוטה מהקונטקסט"""
        user = await user_factory(
            phone_number="+972501111112",
            name="Canceller",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="11112",
        )

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram",
            SenderState.DELIVERY_CONFIRM.value,
            {
                "name": "Canceller",
                "pickup_city": "חיפה",
                "dropoff_city": "באר שבע",
                "urgency": "later",
                "customer_price": 50,
            },
        )

        handler = SenderStateHandler(db_session)
        response, new_state = await handler.handle_message(
            user.id, "telegram", "❌ ביטול"
        )

        assert new_state == SenderState.MENU.value

        context = await state_manager.get_context(user.id, "telegram")
        assert "pickup_city" not in context
        assert "dropoff_city" not in context
        assert "urgency" not in context
        assert "customer_price" not in context
        assert context.get("name") == "Canceller"

    @pytest.mark.asyncio
    async def test_context_preserved_within_delivery_flow(self, db_session, user_factory):
        """מעבר בין שלבים בזרימת משלוח לא מנקה קונטקסט"""
        user = await user_factory(
            phone_number="+972501111113",
            name="FlowUser",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="11113",
        )

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram",
            SenderState.PICKUP_CITY.value,
            {"name": "FlowUser"},
        )

        handler = SenderStateHandler(db_session)
        # מעבר מ-PICKUP_CITY ל-PICKUP_STREET
        response, new_state = await handler.handle_message(
            user.id, "telegram", "תל אביב"
        )

        assert new_state == SenderState.PICKUP_STREET.value
        context = await state_manager.get_context(user.id, "telegram")
        assert context.get("pickup_city") == "תל אביב"


# ============================================================================
# תיקון 2: שלבי אישור לפעולות הרסניות של בעל תחנה
# ============================================================================


class TestStationOwnerConfirmationStates:
    """בדיקות למצבי אישור חדשים של בעל תחנה"""

    @pytest.mark.unit
    def test_confirmation_states_exist(self):
        """מוודא שמצבי האישור קיימים ב-enum"""
        assert hasattr(StationOwnerState, "CONFIRM_REMOVE_DISPATCHER")
        assert hasattr(StationOwnerState, "CONFIRM_REMOVE_BLACKLIST")

    @pytest.mark.unit
    def test_confirmation_transitions_defined(self):
        """מוודא שמעברי אישור מוגדרים"""
        # הסרת סדרן: SELECT -> CONFIRM -> MANAGE
        assert StationOwnerState.CONFIRM_REMOVE_DISPATCHER in (
            STATION_OWNER_TRANSITIONS[StationOwnerState.REMOVE_DISPATCHER_SELECT]
        )
        assert StationOwnerState.MANAGE_DISPATCHERS in (
            STATION_OWNER_TRANSITIONS[StationOwnerState.CONFIRM_REMOVE_DISPATCHER]
        )

        # הסרה מרשימה שחורה: SELECT -> CONFIRM -> VIEW_BLACKLIST
        assert StationOwnerState.CONFIRM_REMOVE_BLACKLIST in (
            STATION_OWNER_TRANSITIONS[StationOwnerState.REMOVE_BLACKLIST_SELECT]
        )
        assert StationOwnerState.VIEW_BLACKLIST in (
            STATION_OWNER_TRANSITIONS[StationOwnerState.CONFIRM_REMOVE_BLACKLIST]
        )


class TestStationOwnerRemoveDispatcherConfirmation:
    """בדיקות לזרימת אישור הסרת סדרן"""

    @pytest.mark.asyncio
    async def test_remove_dispatcher_shows_confirmation(
        self, db_session, station_with_owner_and_dispatcher
    ):
        """בחירת סדרן להסרה מציגה הודעת אישור"""
        station, owner, dispatcher = station_with_owner_and_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram",
            StationOwnerState.REMOVE_DISPATCHER_SELECT.value,
            {"dispatcher_map": {"1": dispatcher.id}},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "הסר 1", None)

        assert new_state == StationOwnerState.CONFIRM_REMOVE_DISPATCHER.value
        assert "אישור" in response.text
        assert "בטוח" in response.text
        assert "Dispatcher Driver" in response.text

    @pytest.mark.asyncio
    async def test_confirm_removes_dispatcher(
        self, db_session, station_with_owner_and_dispatcher
    ):
        """אישור ההסרה מבצע את הפעולה"""
        station, owner, dispatcher = station_with_owner_and_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram",
            StationOwnerState.CONFIRM_REMOVE_DISPATCHER.value,
            {"remove_dispatcher_id": dispatcher.id, "remove_dispatcher_name": "Dispatcher Driver"},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "✅ כן, הסר", None)

        assert new_state == StationOwnerState.MANAGE_DISPATCHERS.value

        # וידוא שהסדרן הוסר
        service = StationService(db_session)
        assert not await service.is_dispatcher(dispatcher.id)

    @pytest.mark.asyncio
    async def test_cancel_does_not_remove_dispatcher(
        self, db_session, station_with_owner_and_dispatcher
    ):
        """ביטול לא מסיר את הסדרן"""
        station, owner, dispatcher = station_with_owner_and_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram",
            StationOwnerState.CONFIRM_REMOVE_DISPATCHER.value,
            {"remove_dispatcher_id": dispatcher.id, "remove_dispatcher_name": "Dispatcher Driver"},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "❌ ביטול", None)

        assert new_state == StationOwnerState.MANAGE_DISPATCHERS.value

        # וידוא שהסדרן עדיין קיים
        service = StationService(db_session)
        assert await service.is_dispatcher(dispatcher.id)


class TestStationOwnerRemoveBlacklistConfirmation:
    """בדיקות לזרימת אישור הסרה מרשימה שחורה"""

    @pytest.mark.asyncio
    async def test_remove_blacklist_shows_confirmation(
        self, db_session, station_with_owner_and_dispatcher, user_factory
    ):
        """בחירת נהג להסרה מהרשימה השחורה מציגה הודעת אישור"""
        station, owner, dispatcher = station_with_owner_and_dispatcher

        # הוספת נהג לרשימה שחורה
        bad_driver = await user_factory(
            phone_number="+972509999999",
            name="Bad Driver",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="90001",
            approval_status=ApprovalStatus.APPROVED,
        )
        service = StationService(db_session)
        await service.add_to_blacklist(station.id, "+972509999999", "אי תשלום")

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram",
            StationOwnerState.REMOVE_BLACKLIST_SELECT.value,
            {"blacklist_map": {"1": bad_driver.id}},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "הסר 1", None)

        assert new_state == StationOwnerState.CONFIRM_REMOVE_BLACKLIST.value
        assert "אישור" in response.text
        assert "בטוח" in response.text

    @pytest.mark.asyncio
    async def test_confirm_removes_from_blacklist(
        self, db_session, station_with_owner_and_dispatcher, user_factory
    ):
        """אישור מסיר את הנהג מהרשימה השחורה"""
        station, owner, dispatcher = station_with_owner_and_dispatcher

        bad_driver = await user_factory(
            phone_number="+972509999998",
            name="Bad Driver 2",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="90002",
            approval_status=ApprovalStatus.APPROVED,
        )
        service = StationService(db_session)
        await service.add_to_blacklist(station.id, "+972509999998", "אי תשלום")

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram",
            StationOwnerState.CONFIRM_REMOVE_BLACKLIST.value,
            {"remove_blacklist_courier_id": bad_driver.id, "remove_blacklist_name": "Bad Driver 2"},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "✅ כן, הסר", None)

        assert new_state == StationOwnerState.VIEW_BLACKLIST.value
        assert not await service.is_blacklisted(station.id, bad_driver.id)

    @pytest.mark.asyncio
    async def test_cancel_keeps_in_blacklist(
        self, db_session, station_with_owner_and_dispatcher, user_factory
    ):
        """ביטול משאיר את הנהג ברשימה השחורה"""
        station, owner, dispatcher = station_with_owner_and_dispatcher

        bad_driver = await user_factory(
            phone_number="+972509999997",
            name="Bad Driver 3",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="90003",
            approval_status=ApprovalStatus.APPROVED,
        )
        service = StationService(db_session)
        await service.add_to_blacklist(station.id, "+972509999997", "אי תשלום")

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram",
            StationOwnerState.CONFIRM_REMOVE_BLACKLIST.value,
            {"remove_blacklist_courier_id": bad_driver.id, "remove_blacklist_name": "Bad Driver 3"},
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "❌ ביטול", None)

        assert new_state == StationOwnerState.VIEW_BLACKLIST.value
        assert await service.is_blacklisted(station.id, bad_driver.id)


# ============================================================================
# תיקון 3: ניקוי rejection_note בהגשה מחדש של שליח
# ============================================================================


class TestRejectionNoteClearOnResubmit:
    """בדיקות לניקוי rejection_note בהגשה מחדש"""

    @pytest.mark.asyncio
    async def test_rejection_note_cleared_on_terms_acceptance(
        self, db_session, user_factory
    ):
        """אישור תקנון מחדש מנקה את הערת הדחייה הישנה"""
        user = await user_factory(
            phone_number="tg:144001",
            name="Rejected Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="144001",
            approval_status=ApprovalStatus.PENDING,
        )
        # דחייה עם הערה
        result = await CourierApprovalService.reject(
            db_session, user.id, rejection_note="התמונות לא ברורות"
        )
        assert result.success is True
        assert user.rejection_note == "התמונות לא ברורות"

        # סימולציה של הגשה מחדש — שליח שנדחה חוזר דרך INITIAL
        # (כמו שקורה בפועל: /start → "הצטרפות למנוי" → COURIER.INITIAL)
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", CourierState.INITIAL.value, context={}
        )

        handler = CourierStateHandler(db_session, platform="telegram")

        # אתחול → שם
        await handler.handle_message(user, "start", None)
        # שם
        await handler.handle_message(user, "ישראל ישראלי", None)
        # מסמך
        await handler.handle_message(user, "", "doc_new")
        # סלפי
        await handler.handle_message(user, "", "selfie_new")
        # קטגוריית רכב
        await handler.handle_message(user, "🚗 רכב 4 מקומות", None)
        # תמונת רכב
        await handler.handle_message(user, "", "vehicle_new")
        # אישור תקנון
        response, new_state = await handler.handle_message(
            user, "קראתי ואני מאשר ✅", None
        )

        assert new_state == CourierState.PENDING_APPROVAL.value
        assert user.approval_status == ApprovalStatus.PENDING
        # הערת הדחייה הישנה חייבת להיות מנוקה
        assert user.rejection_note is None

    @pytest.mark.asyncio
    async def test_rejection_note_none_for_first_time_registration(
        self, db_session, user_factory
    ):
        """רישום ראשוני — rejection_note נשאר None"""
        user = await user_factory(
            phone_number="tg:144002",
            name="New Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="144002",
            approval_status=None,
        )

        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)
        await handler.handle_message(user, "", "doc_file")
        await handler.handle_message(user, "", "selfie_file")
        await handler.handle_message(user, "🚗 רכב 4 מקומות", None)
        await handler.handle_message(user, "", "vehicle_file")
        response, new_state = await handler.handle_message(
            user, "קראתי ואני מאשר ✅", None
        )

        assert new_state == CourierState.PENDING_APPROVAL.value
        assert user.rejection_note is None
