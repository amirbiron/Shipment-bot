"""
בדיקות לשלב 3 - מבנה ההרשאות והתפריטים (Hybrid UX Architecture).

שלב 3.1: תפריט נהג - כפתור סדרן למשתמשים שהם גם סדרנים
שלב 3.2: תפריט סדרן היברידי - הוספת משלוח, משלוחים פעילים, היסטוריה, חיוב ידני
שלב 3.3: פאנל ניהול תחנה - ניהול סדרנים, ארנק תחנה, דוח גבייה, רשימה שחורה
"""
import pytest
from unittest.mock import patch
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.state_machine.states import (
    DispatcherState,
    StationOwnerState,
    DISPATCHER_TRANSITIONS,
    STATION_OWNER_TRANSITIONS,
)
from app.state_machine.dispatcher_handler import DispatcherStateHandler
from app.state_machine.station_owner_handler import StationOwnerStateHandler
from app.state_machine.handlers import CourierStateHandler, MessageResponse
from app.state_machine.manager import StateManager
from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_wallet import StationWallet
from app.db.models.station_blacklist import StationBlacklist
from app.db.models.manual_charge import ManualCharge
from app.db.models.delivery import Delivery, DeliveryStatus
from app.domain.services.station_service import StationService


# ============================================================================
# Fixtures לתחנות
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
async def sample_station_owner(user_factory) -> User:
    """יצירת בעל תחנה לבדיקות"""
    return await user_factory(
        phone_number="+972503333333",
        name="Station Owner",
        role=UserRole.STATION_OWNER,
        platform="telegram",
        telegram_chat_id="30001",
    )


@pytest.fixture
async def sample_dispatcher(user_factory) -> User:
    """יצירת נהג שהוא גם סדרן"""
    return await user_factory(
        phone_number="+972504444444",
        name="Dispatcher Driver",
        role=UserRole.COURIER,
        platform="telegram",
        telegram_chat_id="40001",
        approval_status=ApprovalStatus.APPROVED,
    )


@pytest.fixture
async def station_with_dispatcher(
    db_session, station_factory, sample_station_owner, sample_dispatcher
) -> tuple:
    """יצירת תחנה עם בעלים וסדרן"""
    station = await station_factory(
        name="תחנת בדיקה",
        owner_id=sample_station_owner.id,
    )

    dispatcher_link = StationDispatcher(
        station_id=station.id,
        user_id=sample_dispatcher.id,
    )
    db_session.add(dispatcher_link)
    await db_session.commit()

    return station, sample_station_owner, sample_dispatcher


# ============================================================================
# שלב 3 - State Machine - מעברי מצבים
# ============================================================================


class TestStage3StateDefinitions:
    """בדיקת מצבים ומעברים חדשים [שלב 3]"""

    @pytest.mark.unit
    def test_dispatcher_states_exist(self):
        """מוודא שכל מצבי הסדרן קיימים"""
        assert hasattr(DispatcherState, "MENU")
        assert hasattr(DispatcherState, "ADD_SHIPMENT_PICKUP_CITY")
        assert hasattr(DispatcherState, "VIEW_ACTIVE_SHIPMENTS")
        assert hasattr(DispatcherState, "VIEW_SHIPMENT_HISTORY")
        assert hasattr(DispatcherState, "MANUAL_CHARGE_DRIVER_NAME")

    @pytest.mark.unit
    def test_station_owner_states_exist(self):
        """מוודא שכל מצבי בעל התחנה קיימים"""
        assert hasattr(StationOwnerState, "MENU")
        assert hasattr(StationOwnerState, "MANAGE_DISPATCHERS")
        assert hasattr(StationOwnerState, "ADD_DISPATCHER_PHONE")
        assert hasattr(StationOwnerState, "VIEW_WALLET")
        assert hasattr(StationOwnerState, "COLLECTION_REPORT")
        assert hasattr(StationOwnerState, "VIEW_BLACKLIST")

    @pytest.mark.unit
    def test_dispatcher_transitions_defined(self):
        """מוודא שמעברי מצבים של סדרן מוגדרים"""
        # תפריט -> הוספת משלוח
        assert DispatcherState.ADD_SHIPMENT_PICKUP_CITY in DISPATCHER_TRANSITIONS[DispatcherState.MENU]
        # תפריט -> משלוחים פעילים
        assert DispatcherState.VIEW_ACTIVE_SHIPMENTS in DISPATCHER_TRANSITIONS[DispatcherState.MENU]
        # תפריט -> היסטוריה
        assert DispatcherState.VIEW_SHIPMENT_HISTORY in DISPATCHER_TRANSITIONS[DispatcherState.MENU]
        # תפריט -> חיוב ידני
        assert DispatcherState.MANUAL_CHARGE_DRIVER_NAME in DISPATCHER_TRANSITIONS[DispatcherState.MENU]

    @pytest.mark.unit
    def test_station_owner_transitions_defined(self):
        """מוודא שמעברי מצבים של בעל תחנה מוגדרים"""
        # תפריט -> ניהול סדרנים
        assert StationOwnerState.MANAGE_DISPATCHERS in STATION_OWNER_TRANSITIONS[StationOwnerState.MENU]
        # תפריט -> ארנק
        assert StationOwnerState.VIEW_WALLET in STATION_OWNER_TRANSITIONS[StationOwnerState.MENU]
        # תפריט -> דוח גבייה
        assert StationOwnerState.COLLECTION_REPORT in STATION_OWNER_TRANSITIONS[StationOwnerState.MENU]
        # תפריט -> רשימה שחורה
        assert StationOwnerState.VIEW_BLACKLIST in STATION_OWNER_TRANSITIONS[StationOwnerState.MENU]

    @pytest.mark.unit
    def test_dispatcher_add_shipment_flow(self):
        """מוודא שזרימת הוספת משלוח של סדרן רציפה"""
        flow = [
            DispatcherState.MENU,
            DispatcherState.ADD_SHIPMENT_PICKUP_CITY,
            DispatcherState.ADD_SHIPMENT_PICKUP_STREET,
            DispatcherState.ADD_SHIPMENT_PICKUP_NUMBER,
            DispatcherState.ADD_SHIPMENT_DROPOFF_CITY,
            DispatcherState.ADD_SHIPMENT_DROPOFF_STREET,
            DispatcherState.ADD_SHIPMENT_DROPOFF_NUMBER,
            DispatcherState.ADD_SHIPMENT_DROPOFF_APARTMENT,
            DispatcherState.ADD_SHIPMENT_DESCRIPTION,
            DispatcherState.ADD_SHIPMENT_FEE,
            DispatcherState.ADD_SHIPMENT_CONFIRM,
        ]
        for i in range(len(flow) - 1):
            assert flow[i + 1] in DISPATCHER_TRANSITIONS[flow[i]], (
                f"Missing transition: {flow[i].value} -> {flow[i + 1].value}"
            )

    @pytest.mark.unit
    def test_dispatcher_manual_charge_flow(self):
        """מוודא שזרימת חיוב ידני רציפה"""
        flow = [
            DispatcherState.MENU,
            DispatcherState.MANUAL_CHARGE_DRIVER_NAME,
            DispatcherState.MANUAL_CHARGE_AMOUNT,
            DispatcherState.MANUAL_CHARGE_DESCRIPTION,
            DispatcherState.MANUAL_CHARGE_CONFIRM,
        ]
        for i in range(len(flow) - 1):
            assert flow[i + 1] in DISPATCHER_TRANSITIONS[flow[i]], (
                f"Missing transition: {flow[i].value} -> {flow[i + 1].value}"
            )


# ============================================================================
# שלב 3.1 - תפריט נהג - כפתור סדרן
# ============================================================================


class TestStage31DriverMenu:
    """בדיקות לתפריט הנהג עם כפתור סדרן [שלב 3.1]"""

    @pytest.mark.asyncio
    async def test_courier_menu_shows_dispatcher_button_for_dispatchers(
        self, db_session, station_with_dispatcher
    ):
        """נהג שהוא סדרן רואה כפתור 'תפריט סדרן' בתפריט"""
        station, owner, dispatcher = station_with_dispatcher

        handler = CourierStateHandler(db_session, platform="telegram")

        # מאפסים את המצב לתפריט
        state_manager = StateManager(db_session)
        from app.state_machine.states import CourierState
        await state_manager.force_state(
            dispatcher.id, "telegram", CourierState.MENU.value, {}
        )

        response, new_state = await handler.handle_message(dispatcher, "תפריט", None)

        # הכפתור צריך להופיע
        assert response.keyboard is not None
        all_buttons = [btn for row in response.keyboard for btn in row]
        assert "🏪 תפריט סדרן" in all_buttons

    @pytest.mark.asyncio
    async def test_courier_menu_no_dispatcher_button_for_regular_courier(
        self, db_session, user_factory
    ):
        """נהג רגיל (לא סדרן) לא רואה כפתור סדרן"""
        courier = await user_factory(
            phone_number="+972505555555",
            name="Regular Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="50001",
            approval_status=ApprovalStatus.APPROVED,
        )

        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)
        from app.state_machine.states import CourierState
        await state_manager.force_state(
            courier.id, "telegram", CourierState.MENU.value, {}
        )

        response, new_state = await handler.handle_message(courier, "תפריט", None)

        # הכפתור לא צריך להופיע
        all_buttons = [btn for row in response.keyboard for btn in row]
        assert "🏪 תפריט סדרן" not in all_buttons


# ============================================================================
# באג #87 - כפתור "חזרה לתפריט" בארנק שליח מציג שוב את הארנק
# ============================================================================


class TestCourierWalletBackButton:
    """בדיקות לכפתור חזרה לתפריט ממסך הארנק (issue #87)"""

    @pytest.mark.asyncio
    async def test_back_from_wallet_returns_to_menu(
        self, db_session, user_factory
    ):
        """לחיצה על 'חזרה לתפריט' מארנק מחזירה לתפריט ולא מציגה שוב ארנק"""
        from app.state_machine.states import CourierState

        courier = await user_factory(
            phone_number="+972506666666",
            name="Wallet Tester",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="60001",
            approval_status=ApprovalStatus.APPROVED,
        )

        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            courier.id, "telegram", CourierState.VIEW_WALLET.value, {}
        )

        response, new_state = await handler.handle_message(
            courier, "🔙 חזרה לתפריט", None
        )

        # חייב לחזור לתפריט - לא להישאר בארנק
        assert new_state == CourierState.MENU.value
        assert "תפריט שליח" in response.text

    @pytest.mark.asyncio
    async def test_wallet_displays_when_no_back_button(
        self, db_session, user_factory
    ):
        """כניסה רגילה לארנק מציגה פרטי ארנק כרגיל"""
        from app.state_machine.states import CourierState

        courier = await user_factory(
            phone_number="+972506666667",
            name="Wallet Viewer",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="60002",
            approval_status=ApprovalStatus.APPROVED,
        )

        handler = CourierStateHandler(db_session, platform="telegram")
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            courier.id, "telegram", CourierState.MENU.value, {}
        )

        # נכנסים לארנק דרך התפריט
        response, new_state = await handler.handle_message(
            courier, "💰 מצב הארנק", None
        )

        assert new_state == CourierState.VIEW_WALLET.value
        assert "פרטי הארנק" in response.text


# ============================================================================
# שלב 3.2 - תפריט סדרן היברידי - Handlers
# ============================================================================


class TestStage32DispatcherHandlers:
    """בדיקות ל-handlers של סדרן [שלב 3.2]"""

    @pytest.mark.asyncio
    async def test_dispatcher_menu_shows_options(
        self, db_session, station_with_dispatcher
    ):
        """תפריט סדרן מציג 4 אפשרויות"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            dispatcher.id, "telegram", DispatcherState.MENU.value, {}
        )

        handler = DispatcherStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(dispatcher, "תפריט", None)

        assert new_state == DispatcherState.MENU.value
        assert "תפריט סדרן" in response.text
        all_buttons = [btn for row in response.keyboard for btn in row]
        assert "➕ הוספת משלוח" in all_buttons
        assert "📦 משלוחים פעילים" in all_buttons
        assert "📋 היסטוריית משלוחים" in all_buttons
        assert "💳 חיוב ידני" in all_buttons

    @pytest.mark.asyncio
    async def test_dispatcher_add_shipment_full_flow(
        self, db_session, station_with_dispatcher
    ):
        """זרימת הוספת משלוח מלאה ע"י סדרן"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            dispatcher.id, "telegram", DispatcherState.MENU.value, {}
        )

        handler = DispatcherStateHandler(db_session, station.id)

        # 1. בחירת "הוספת משלוח" מהתפריט
        response, new_state = await handler.handle_message(
            dispatcher, "➕ הוספת משלוח", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_PICKUP_CITY.value

        # 2. עיר איסוף
        response, new_state = await handler.handle_message(
            dispatcher, "תל אביב", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_PICKUP_STREET.value

        # 3. רחוב איסוף
        response, new_state = await handler.handle_message(
            dispatcher, "הרצל", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_PICKUP_NUMBER.value

        # 4. מספר בית איסוף
        response, new_state = await handler.handle_message(
            dispatcher, "10", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_DROPOFF_CITY.value

        # 5. עיר יעד
        response, new_state = await handler.handle_message(
            dispatcher, "ירושלים", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_DROPOFF_STREET.value

        # 6. רחוב יעד
        response, new_state = await handler.handle_message(
            dispatcher, "יפו", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_DROPOFF_NUMBER.value

        # 7. מספר בית יעד
        response, new_state = await handler.handle_message(
            dispatcher, "5", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_DROPOFF_APARTMENT.value

        # 7.5 דירה/יחידה ביעד (דלג)
        response, new_state = await handler.handle_message(
            dispatcher, "דלג", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_DESCRIPTION.value

        # 8. תיאור
        response, new_state = await handler.handle_message(
            dispatcher, "חבילה קטנה", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_FEE.value

        # 9. מחיר
        response, new_state = await handler.handle_message(
            dispatcher, "50", None
        )
        assert new_state == DispatcherState.ADD_SHIPMENT_CONFIRM.value
        assert "סיכום" in response.text
        assert "50" in response.text

        # 10. אישור
        response, new_state = await handler.handle_message(
            dispatcher, "✅ אישור ושליחה", None
        )
        assert new_state == DispatcherState.MENU.value
        assert "בהצלחה" in response.text

        # וידוא שהמשלוח נוצר בDB עם שיוך לתחנה
        result = await db_session.execute(
            select(Delivery).where(Delivery.station_id == station.id)
        )
        delivery = result.scalar_one_or_none()
        assert delivery is not None
        assert delivery.station_id == station.id
        assert delivery.fee == 50.0
        assert delivery.status == DeliveryStatus.OPEN

    @pytest.mark.asyncio
    async def test_dispatcher_add_shipment_cancel(
        self, db_session, station_with_dispatcher
    ):
        """ביטול הוספת משלוח"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            dispatcher.id, "telegram",
            DispatcherState.ADD_SHIPMENT_CONFIRM.value,
            {"pickup_address": "test", "dropoff_address": "test", "fee": 30}
        )

        handler = DispatcherStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(
            dispatcher, "❌ ביטול", None
        )
        assert new_state == DispatcherState.MENU.value
        assert "בוטל" in response.text

    @pytest.mark.asyncio
    async def test_dispatcher_view_active_empty(
        self, db_session, station_with_dispatcher
    ):
        """צפייה במשלוחים פעילים כשאין משלוחים"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            dispatcher.id, "telegram", DispatcherState.MENU.value, {}
        )

        handler = DispatcherStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(
            dispatcher, "משלוחים פעילים", None
        )
        assert "אין משלוחים פעילים" in response.text

    @pytest.mark.asyncio
    async def test_dispatcher_manual_charge_flow(
        self, db_session, station_with_dispatcher
    ):
        """זרימת חיוב ידני מלאה"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            dispatcher.id, "telegram", DispatcherState.MENU.value, {}
        )

        handler = DispatcherStateHandler(db_session, station.id)

        # 1. בחירת חיוב ידני
        response, new_state = await handler.handle_message(
            dispatcher, "💳 חיוב ידני", None
        )
        assert new_state == DispatcherState.MANUAL_CHARGE_DRIVER_NAME.value

        # 2. שם הנהג
        response, new_state = await handler.handle_message(
            dispatcher, "דוד כהן", None
        )
        assert new_state == DispatcherState.MANUAL_CHARGE_AMOUNT.value

        # 3. סכום
        response, new_state = await handler.handle_message(
            dispatcher, "100", None
        )
        assert new_state == DispatcherState.MANUAL_CHARGE_DESCRIPTION.value

        # 4. תיאור
        response, new_state = await handler.handle_message(
            dispatcher, "משלוח מנתניה לחיפה", None
        )
        assert new_state == DispatcherState.MANUAL_CHARGE_CONFIRM.value
        assert "דוד כהן" in response.text
        assert "100" in response.text

        # 5. אישור
        response, new_state = await handler.handle_message(
            dispatcher, "✅ אישור", None
        )
        assert new_state == DispatcherState.MENU.value
        assert "נרשם בהצלחה" in response.text

        # וידוא שהחיוב נשמר בDB
        result = await db_session.execute(
            select(ManualCharge).where(ManualCharge.station_id == station.id)
        )
        charge = result.scalar_one_or_none()
        assert charge is not None
        assert charge.driver_name == "דוד כהן"
        assert charge.amount == 100.0


# ============================================================================
# שלב 3.3 - פאנל ניהול תחנה - Handlers
# ============================================================================


class TestStage33StationOwnerHandlers:
    """בדיקות ל-handlers של בעל תחנה [שלב 3.3]"""

    @pytest.mark.asyncio
    async def test_station_menu_shows_options(
        self, db_session, station_with_dispatcher
    ):
        """תפריט בעל תחנה מציג את כל האפשרויות"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram", StationOwnerState.MENU.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "תפריט", None)

        assert new_state == StationOwnerState.MENU.value
        assert "פאנל ניהול" in response.text
        all_buttons = [btn for row in response.keyboard for btn in row]
        assert "👥 ניהול סדרנים" in all_buttons
        assert "💰 ארנק תחנה" in all_buttons
        assert "📊 דוח גבייה" in all_buttons
        assert "🚫 רשימה שחורה" in all_buttons

    @pytest.mark.asyncio
    async def test_station_manage_dispatchers(
        self, db_session, station_with_dispatcher
    ):
        """ניהול סדרנים - הצגת רשימה"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram", StationOwnerState.MENU.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "ניהול סדרנים", None)

        assert new_state == StationOwnerState.MANAGE_DISPATCHERS.value
        assert "ניהול סדרנים" in response.text
        # הסדרן שנוסף ב-fixture צריך להופיע
        assert "Dispatcher Driver" in response.text

    @pytest.mark.asyncio
    async def test_station_view_wallet(
        self, db_session, station_with_dispatcher
    ):
        """צפייה בארנק תחנה"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram", StationOwnerState.MENU.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "ארנק תחנה", None)

        assert new_state == StationOwnerState.VIEW_WALLET.value
        assert "ארנק תחנה" in response.text
        assert "10%" in response.text  # שיעור עמלה

    @pytest.mark.asyncio
    async def test_station_collection_report_empty(
        self, db_session, station_with_dispatcher
    ):
        """דוח גבייה ריק"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram", StationOwnerState.MENU.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "דוח גבייה", None)

        assert new_state == StationOwnerState.COLLECTION_REPORT.value
        assert "דוח גבייה" in response.text
        assert "אין חובות" in response.text

    @pytest.mark.asyncio
    async def test_station_blacklist_empty(
        self, db_session, station_with_dispatcher
    ):
        """רשימה שחורה ריקה"""
        station, owner, dispatcher = station_with_dispatcher

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram", StationOwnerState.MENU.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id)
        response, new_state = await handler.handle_message(owner, "רשימה שחורה", None)

        assert new_state == StationOwnerState.VIEW_BLACKLIST.value
        assert "רשימה שחורה" in response.text
        assert "ריקה" in response.text


# ============================================================================
# שלב 3 - StationService - Domain Layer
# ============================================================================


class TestStationService:
    """בדיקות ל-StationService"""

    @pytest.mark.asyncio
    async def test_create_station(self, db_session, user_factory):
        """יצירת תחנה חדשה עם ארנק"""
        owner = await user_factory(
            phone_number="+972506666666",
            name="New Owner",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="60001",
        )

        service = StationService(db_session)
        station = await service.create_station("תחנה חדשה", owner.id)
        # create_station עושה flush בלבד - הקוראים אחראים על commit
        await db_session.commit()
        await db_session.refresh(station)

        assert station.id is not None
        assert station.name == "תחנה חדשה"
        assert station.owner_id == owner.id

        # וידוא שנוצר ארנק
        wallet = await service.get_station_wallet(station.id)
        assert wallet is not None
        assert wallet.balance == 0.0
        assert float(wallet.commission_rate) == pytest.approx(0.10)

    @pytest.mark.asyncio
    async def test_add_and_remove_dispatcher(
        self, db_session, user_factory, station_factory
    ):
        """הוספה והסרה של סדרן"""
        owner = await user_factory(
            phone_number="+972507777777",
            name="Owner",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="70001",
        )
        courier = await user_factory(
            phone_number="+972508888888",
            name="Courier For Dispatch",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80001",
            approval_status=ApprovalStatus.APPROVED,
        )

        station = await station_factory(name="תחנת בדיקה", owner_id=owner.id)
        service = StationService(db_session)

        # הוספה
        success, msg = await service.add_dispatcher(station.id, "+972508888888")
        assert success is True

        # בדיקה שהוא סדרן
        assert await service.is_dispatcher(courier.id)

        # הוספה כפולה נכשלת
        success, msg = await service.add_dispatcher(station.id, "+972508888888")
        assert success is False

        # הסרה
        success, msg = await service.remove_dispatcher(station.id, courier.id)
        assert success is True

    @pytest.mark.asyncio
    async def test_manual_charge_updates_wallet(
        self, db_session, station_with_dispatcher
    ):
        """חיוב ידני מעדכן את ארנק התחנה"""
        station, owner, dispatcher = station_with_dispatcher

        service = StationService(db_session)

        # יצירת חיוב ידני
        charge = await service.create_manual_charge(
            station_id=station.id,
            dispatcher_id=dispatcher.id,
            driver_name="נהג א",
            amount=50.0,
            description="משלוח מרכז",
        )

        assert charge.amount == 50.0

        # ארנק התחנה צריך לעלות ב-50
        wallet = await service.get_station_wallet(station.id)
        assert wallet.balance == 50.0

    @pytest.mark.asyncio
    async def test_station_commission_credit(
        self, db_session, station_with_dispatcher, delivery_factory
    ):
        """עמלת תחנה (10%) נזקפת בעת השלמת משלוח"""
        station, owner, dispatcher = station_with_dispatcher

        # יצירת משלוח
        delivery = await delivery_factory(
            sender_id=dispatcher.id,
            fee=100.0,
        )

        service = StationService(db_session)
        await service.credit_station_commission(
            station_id=station.id,
            delivery_id=delivery.id,
            fee=100.0,
        )

        wallet = await service.get_station_wallet(station.id)
        assert wallet.balance == 10.0  # 10% מ-100

    @pytest.mark.asyncio
    async def test_blacklist_operations(
        self, db_session, user_factory, station_with_dispatcher
    ):
        """הוספה והסרה מרשימה שחורה"""
        station, owner, dispatcher = station_with_dispatcher

        bad_driver = await user_factory(
            phone_number="+972509999999",
            name="Bad Driver",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="90001",
            approval_status=ApprovalStatus.APPROVED,
        )

        service = StationService(db_session)

        # הוספה לרשימה שחורה
        success, msg = await service.add_to_blacklist(
            station.id, "+972509999999", "אי תשלום חודשיים"
        )
        assert success is True

        # בדיקה שהנהג חסום
        assert await service.is_blacklisted(station.id, bad_driver.id)

        # הוספה כפולה נכשלת
        success, msg = await service.add_to_blacklist(
            station.id, "+972509999999", "סיבה נוספת"
        )
        assert success is False

        # הסרה
        success, msg = await service.remove_from_blacklist(station.id, bad_driver.id)
        assert success is True
        assert not await service.is_blacklisted(station.id, bad_driver.id)

    @pytest.mark.asyncio
    async def test_collection_report_with_charges(
        self, db_session, station_with_dispatcher
    ):
        """דוח גבייה עם חיובים"""
        station, owner, dispatcher = station_with_dispatcher

        service = StationService(db_session)

        # יצירת חיובים
        await service.create_manual_charge(station.id, dispatcher.id, "נהג א", 100.0, "משלוח 1")
        await service.create_manual_charge(station.id, dispatcher.id, "נהג א", 50.0, "משלוח 2")
        await service.create_manual_charge(station.id, dispatcher.id, "נהג ב", 75.0, "משלוח 3")

        report = await service.get_collection_report(station.id)

        assert len(report) == 2
        driver_a = next(r for r in report if r["driver_name"] == "נהג א")
        assert driver_a["total_debt"] == 150.0
        driver_b = next(r for r in report if r["driver_name"] == "נהג ב")
        assert driver_b["total_debt"] == 75.0

    @pytest.mark.asyncio
    async def test_get_station_active_deliveries(
        self, db_session, station_with_dispatcher, delivery_factory
    ):
        """קבלת משלוחים פעילים של תחנה"""
        station, owner, dispatcher = station_with_dispatcher

        # יצירת משלוחים שייכים לתחנה
        d1 = Delivery(
            sender_id=dispatcher.id,
            pickup_address="כתובת 1",
            dropoff_address="כתובת 2",
            fee=30.0,
            status=DeliveryStatus.OPEN,
            station_id=station.id,
        )
        d2 = Delivery(
            sender_id=dispatcher.id,
            pickup_address="כתובת 3",
            dropoff_address="כתובת 4",
            fee=40.0,
            status=DeliveryStatus.DELIVERED,
            station_id=station.id,
        )
        db_session.add_all([d1, d2])
        await db_session.commit()

        service = StationService(db_session)
        active = await service.get_station_active_deliveries(station.id)

        # רק משלוח 1 צריך להופיע (OPEN)
        assert len(active) == 1
        assert active[0].fee == 30.0


# ============================================================================
# שלב 3 - מודלים חדשים
# ============================================================================


class TestStage3Models:
    """בדיקות למודלים החדשים של שלב 3"""

    @pytest.mark.asyncio
    async def test_station_model(self, db_session, user_factory):
        """מודל תחנה"""
        owner = await user_factory(
            phone_number="+972501110001",
            name="Model Test Owner",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="110001",
        )

        station = Station(name="תחנת מודל", owner_id=owner.id)
        db_session.add(station)
        await db_session.commit()
        await db_session.refresh(station)

        assert station.id is not None
        assert station.name == "תחנת מודל"
        assert station.is_active is True

    @pytest.mark.asyncio
    async def test_user_role_station_owner(self, db_session, user_factory):
        """מוודא שתפקיד STATION_OWNER קיים ונשמר"""
        user = await user_factory(
            phone_number="+972501110002",
            name="Station Owner Test",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="110002",
        )
        assert user.role == UserRole.STATION_OWNER

    @pytest.mark.asyncio
    async def test_delivery_station_id_field(self, db_session, user_factory):
        """מוודא ששדה station_id קיים במשלוח"""
        sender = await user_factory(
            phone_number="+972501110003",
            name="Delivery Sender",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="110003",
        )

        delivery = Delivery(
            sender_id=sender.id,
            pickup_address="כתובת",
            dropoff_address="כתובת",
            fee=10.0,
            station_id=None,  # משלוח ללא תחנה
        )
        db_session.add(delivery)
        await db_session.commit()
        await db_session.refresh(delivery)

        assert delivery.station_id is None


# ============================================================================
# תיקון באג: יצירת תחנה לא עדכנה תפקיד + recovery
# ============================================================================


class TestStationCreationAPI:
    """בדיקות ל-API יצירת תחנה - אטומיות ו-recovery של תפקיד"""

    @pytest.mark.asyncio
    async def test_create_station_sets_role_atomically(
        self, test_client, db_session, user_factory
    ):
        """יצירת תחנה דרך API מעדכנת תפקיד ל-STATION_OWNER"""
        user = await user_factory(
            phone_number="+972501230001",
            name="Atomic Owner",
            role=UserRole.SENDER,
            platform="whatsapp",
        )

        with patch.object(settings, "ADMIN_API_KEY", "test-key"):
            response = await test_client.post(
                "/api/stations/",
                json={"name": "תחנת אטומיות", "owner_phone": "0501230001"},
                headers={"X-Admin-API-Key": "test-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "תחנת אטומיות"

        # וידוא שהתפקיד עודכן
        await db_session.refresh(user)
        assert user.role == UserRole.STATION_OWNER

    @pytest.mark.asyncio
    async def test_existing_station_fixes_role(
        self, test_client, db_session, user_factory
    ):
        """
        אם לתחנה כבר קיימת אבל התפקיד לא עודכן (באג ישן),
        הקריאה ל-API מתקנת את התפקיד ל-STATION_OWNER.
        """
        # יצירת משתמש עם תפקיד SENDER (סימולציה של באג ישן)
        user = await user_factory(
            phone_number="+972501230002",
            name="Broken Owner",
            role=UserRole.SENDER,
            platform="whatsapp",
        )

        # יצירת תחנה ישירות בDB בלי לעדכן תפקיד (סימולציה של באג)
        station = Station(name="תחנה שבורה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        # ניסיון ליצור תחנה - צריך להיכשל עם 400 אבל לתקן את הרול
        with patch.object(settings, "ADMIN_API_KEY", "test-key"):
            response = await test_client.post(
                "/api/stations/",
                json={"name": "תחנה כפולה", "owner_phone": "0501230002"},
                headers={"X-Admin-API-Key": "test-key"},
            )
        assert response.status_code == 400

        # וידוא שהתפקיד תוקן למרות השגיאה
        await db_session.refresh(user)
        assert user.role == UserRole.STATION_OWNER
