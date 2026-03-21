"""
בדיקות QA — תפקידי משתמשים (Roles)

מכסה:
- שלמות enum UserRole ו-ApprovalStatus
- תכונות User (is_approved_courier, is_pending_courier)
- מכונות מצבים — prefixes, MENU state, INITIAL state
- מפות TRANSITIONS — כל state קיים ומוצג כיעד לפחות פעם אחת
- StateManager — force_state ו-get_current_state
- יצירת משתמשים בכל תפקיד ב-DB
- בידוד states בין תפקידים
"""
import pytest

from app.db.models.user import User, UserRole, ApprovalStatus
from app.state_machine.states import (
    SenderState,
    CourierState,
    DispatcherState,
    StationOwnerState,
    DriverState,
    AdminState,
    SENDER_TRANSITIONS,
    COURIER_TRANSITIONS,
    DISPATCHER_TRANSITIONS,
    STATION_OWNER_TRANSITIONS,
    DRIVER_TRANSITIONS,
    ADMIN_TRANSITIONS,
)
from app.state_machine.manager import StateManager


# ============================================================================
# UserRole Enum
# ============================================================================


class TestUserRoleEnum:
    """ולידציה של enum UserRole"""

    @pytest.mark.unit
    def test_all_roles_defined(self) -> None:
        """כל חמשת התפקידים חייבים להיות מוגדרים"""
        roles = {r.value for r in UserRole}
        assert "sender" in roles
        assert "courier" in roles
        assert "admin" in roles
        assert "station_owner" in roles
        assert "driver" in roles

    @pytest.mark.unit
    def test_role_count(self) -> None:
        """בדיקה שמספר התפקידים הוא בדיוק 5"""
        assert len(UserRole) == 5

    @pytest.mark.unit
    def test_roles_are_strings(self) -> None:
        """UserRole הוא str enum — ניתן להשוות עם מחרוזות"""
        assert UserRole.SENDER == "sender"
        assert UserRole.COURIER == "courier"
        assert UserRole.ADMIN == "admin"
        assert UserRole.STATION_OWNER == "station_owner"
        assert UserRole.DRIVER == "driver"

    @pytest.mark.unit
    def test_role_lookup_by_value(self) -> None:
        """בדיקת בנייה מערך מחרוזת"""
        assert UserRole("sender") is UserRole.SENDER
        assert UserRole("courier") is UserRole.COURIER
        assert UserRole("admin") is UserRole.ADMIN
        assert UserRole("station_owner") is UserRole.STATION_OWNER
        assert UserRole("driver") is UserRole.DRIVER

    @pytest.mark.unit
    def test_invalid_role_raises(self) -> None:
        """ערך לא חוקי מעלה ValueError"""
        with pytest.raises(ValueError):
            UserRole("unknown_role")


# ============================================================================
# ApprovalStatus Enum
# ============================================================================


class TestApprovalStatusEnum:
    """ולידציה של enum ApprovalStatus"""

    @pytest.mark.unit
    def test_all_statuses_defined(self) -> None:
        """כל ארבעת הסטטוסים חייבים להיות מוגדרים"""
        statuses = {s.value for s in ApprovalStatus}
        assert "pending" in statuses
        assert "approved" in statuses
        assert "rejected" in statuses
        assert "blocked" in statuses

    @pytest.mark.unit
    def test_status_count(self) -> None:
        assert len(ApprovalStatus) == 4

    @pytest.mark.unit
    def test_statuses_are_strings(self) -> None:
        assert ApprovalStatus.PENDING == "pending"
        assert ApprovalStatus.APPROVED == "approved"
        assert ApprovalStatus.REJECTED == "rejected"
        assert ApprovalStatus.BLOCKED == "blocked"


# ============================================================================
# User model properties
# ============================================================================


class TestUserModelProperties:
    """תכונות User.is_approved_courier ו-is_pending_courier"""

    @pytest.mark.unit
    def test_approved_courier(self) -> None:
        """שליח מאושר → is_approved_courier=True"""
        user = User(
            id=1,
            phone_number="+972500000001",
            role=UserRole.COURIER,
            platform="whatsapp",
            approval_status=ApprovalStatus.APPROVED,
        )
        assert user.is_approved_courier is True
        assert user.is_pending_courier is False

    @pytest.mark.unit
    def test_pending_courier(self) -> None:
        """שליח ממתין → is_pending_courier=True"""
        user = User(
            id=2,
            phone_number="+972500000002",
            role=UserRole.COURIER,
            platform="whatsapp",
            approval_status=ApprovalStatus.PENDING,
        )
        assert user.is_pending_courier is True
        assert user.is_approved_courier is False

    @pytest.mark.unit
    def test_sender_not_approved_courier(self) -> None:
        """שולח — שני הדגלים שקריים"""
        user = User(
            id=3,
            phone_number="+972500000003",
            role=UserRole.SENDER,
            platform="whatsapp",
        )
        assert user.is_approved_courier is False
        assert user.is_pending_courier is False

    @pytest.mark.unit
    def test_rejected_courier_not_approved(self) -> None:
        """שליח שנדחה — לא מאושר"""
        user = User(
            id=4,
            phone_number="+972500000004",
            role=UserRole.COURIER,
            platform="whatsapp",
            approval_status=ApprovalStatus.REJECTED,
        )
        assert user.is_approved_courier is False
        assert user.is_pending_courier is False

    @pytest.mark.unit
    def test_blocked_courier_not_approved(self) -> None:
        """שליח חסום — לא מאושר"""
        user = User(
            id=5,
            phone_number="+972500000005",
            role=UserRole.COURIER,
            platform="whatsapp",
            approval_status=ApprovalStatus.BLOCKED,
        )
        assert user.is_approved_courier is False
        assert user.is_pending_courier is False

    @pytest.mark.unit
    def test_admin_not_courier(self) -> None:
        """אדמין — שני דגלי שליח שקריים"""
        user = User(
            id=6,
            phone_number="+972500000006",
            role=UserRole.ADMIN,
            platform="telegram",
        )
        assert user.is_approved_courier is False
        assert user.is_pending_courier is False


# ============================================================================
# State Machine — State Classes
# ============================================================================


class TestStateClasses:
    """בדיקת מבנה מחלקות State לכל תפקיד"""

    @pytest.mark.unit
    def test_sender_has_menu_state(self) -> None:
        assert SenderState.MENU.value == "SENDER.MENU"

    @pytest.mark.unit
    def test_sender_has_initial_state(self) -> None:
        assert SenderState.INITIAL.value == "INITIAL"

    @pytest.mark.unit
    def test_courier_has_menu_state(self) -> None:
        assert CourierState.MENU.value == "COURIER.MENU"

    @pytest.mark.unit
    def test_courier_has_initial_state(self) -> None:
        assert CourierState.INITIAL.value == "COURIER.INITIAL"

    @pytest.mark.unit
    def test_dispatcher_has_menu_state(self) -> None:
        assert DispatcherState.MENU.value == "DISPATCHER.MENU"

    @pytest.mark.unit
    def test_station_owner_has_menu_state(self) -> None:
        assert StationOwnerState.MENU.value == "STATION.MENU"

    @pytest.mark.unit
    def test_driver_has_menu_state(self) -> None:
        assert DriverState.MENU.value == "DRIVER.MENU"

    @pytest.mark.unit
    def test_driver_has_initial_state(self) -> None:
        assert DriverState.INITIAL.value == "DRIVER.INITIAL"

    @pytest.mark.unit
    def test_admin_has_menu_state(self) -> None:
        assert AdminState.MENU.value == "ADMIN.MENU"

    @pytest.mark.unit
    def test_sender_state_prefixes(self) -> None:
        """כל states של שולח (מלבד INITIAL) מתחילים ב-SENDER."""
        for state in SenderState:
            if state == SenderState.INITIAL:
                assert state.value == "INITIAL"
            else:
                assert state.value.startswith("SENDER."), (
                    f"SenderState.{state.name} = {state.value!r} — לא מתחיל ב-SENDER."
                )

    @pytest.mark.unit
    def test_courier_state_prefixes(self) -> None:
        """כל states של שליח מתחילים ב-COURIER."""
        for state in CourierState:
            assert state.value.startswith("COURIER."), (
                f"CourierState.{state.name} = {state.value!r} — לא מתחיל ב-COURIER."
            )

    @pytest.mark.unit
    def test_dispatcher_state_prefixes(self) -> None:
        """כל states של סדרן מתחילים ב-DISPATCHER."""
        for state in DispatcherState:
            assert state.value.startswith("DISPATCHER."), (
                f"DispatcherState.{state.name} = {state.value!r} — לא מתחיל ב-DISPATCHER."
            )

    @pytest.mark.unit
    def test_station_owner_state_prefixes(self) -> None:
        """כל states של בעל תחנה מתחילים ב-STATION."""
        for state in StationOwnerState:
            assert state.value.startswith("STATION."), (
                f"StationOwnerState.{state.name} = {state.value!r} — לא מתחיל ב-STATION."
            )

    @pytest.mark.unit
    def test_driver_state_prefixes(self) -> None:
        """כל states של נהג מתחילים ב-DRIVER."""
        for state in DriverState:
            assert state.value.startswith("DRIVER."), (
                f"DriverState.{state.name} = {state.value!r} — לא מתחיל ב-DRIVER."
            )

    @pytest.mark.unit
    def test_admin_state_prefixes(self) -> None:
        """כל states של אדמין מתחילים ב-ADMIN."""
        for state in AdminState:
            assert state.value.startswith("ADMIN."), (
                f"AdminState.{state.name} = {state.value!r} — לא מתחיל ב-ADMIN."
            )

    @pytest.mark.unit
    def test_no_state_value_collision_between_roles(self) -> None:
        """אין ערך state זהה בין תפקידים שונים"""
        all_values: list[str] = []
        for enum_cls in (
            CourierState,
            DispatcherState,
            StationOwnerState,
            DriverState,
            AdminState,
        ):
            all_values.extend(s.value for s in enum_cls)

        # SenderState.INITIAL = "INITIAL" — לגיטימי כנקודת כניסה ראשונה
        sender_values = [s.value for s in SenderState if s != SenderState.INITIAL]
        all_values.extend(sender_values)

        assert len(all_values) == len(set(all_values)), (
            "נמצאו ערכי state כפולים בין תפקידים שונים"
        )


# ============================================================================
# TRANSITIONS — שלמות מפות המעברים
# ============================================================================


class TestTransitions:
    """בדיקת שלמות מפות TRANSITIONS לכל תפקיד"""

    def _all_target_states(self, transitions: dict) -> set:
        """כל states שמופיעים כיעד במפת TRANSITIONS"""
        targets: set = set()
        for state_list in transitions.values():
            targets.update(state_list)
        return targets

    @pytest.mark.unit
    def test_sender_transitions_cover_all_states(self) -> None:
        """כל SenderState מלבד INITIAL מוגדר כמקור או יעד ב-SENDER_TRANSITIONS"""
        all_states = set(SenderState) - {SenderState.INITIAL}
        legacy_states = {
            SenderState.DELIVERY_COLLECT_PICKUP,
            SenderState.DELIVERY_COLLECT_PICKUP_CONTACT,
            SenderState.DELIVERY_COLLECT_PICKUP_NOTES,
            SenderState.DELIVERY_COLLECT_DROPOFF_MODE,
            SenderState.DELIVERY_COLLECT_DROPOFF_ADDRESS,
            SenderState.DELIVERY_COLLECT_DROPOFF_CONTACT,
            SenderState.DELIVERY_COLLECT_DROPOFF_NOTES,
        }
        active_states = all_states - legacy_states

        keys = set(SENDER_TRANSITIONS.keys())
        targets = self._all_target_states(SENDER_TRANSITIONS)
        covered = keys | targets

        for state in active_states:
            assert state in covered, (
                f"SenderState.{state.name} לא מכוסה ב-SENDER_TRANSITIONS"
            )

    @pytest.mark.unit
    def test_courier_transitions_cover_all_states(self) -> None:
        """כל CourierState מוגדר כמקור או יעד ב-COURIER_TRANSITIONS"""
        keys = set(COURIER_TRANSITIONS.keys())
        targets = self._all_target_states(COURIER_TRANSITIONS)
        covered = keys | targets

        for state in CourierState:
            assert state in covered, (
                f"CourierState.{state.name} לא מכוסה ב-COURIER_TRANSITIONS"
            )

    @pytest.mark.unit
    def test_dispatcher_transitions_cover_all_states(self) -> None:
        """כל DispatcherState מוגדר כמקור או יעד ב-DISPATCHER_TRANSITIONS"""
        keys = set(DISPATCHER_TRANSITIONS.keys())
        targets = self._all_target_states(DISPATCHER_TRANSITIONS)
        covered = keys | targets

        for state in DispatcherState:
            assert state in covered, (
                f"DispatcherState.{state.name} לא מכוסה ב-DISPATCHER_TRANSITIONS"
            )

    @pytest.mark.unit
    def test_station_owner_transitions_cover_all_states(self) -> None:
        """כל StationOwnerState מוגדר כמקור או יעד ב-STATION_OWNER_TRANSITIONS"""
        keys = set(STATION_OWNER_TRANSITIONS.keys())
        targets = self._all_target_states(STATION_OWNER_TRANSITIONS)
        covered = keys | targets

        for state in StationOwnerState:
            assert state in covered, (
                f"StationOwnerState.{state.name} לא מכוסה ב-STATION_OWNER_TRANSITIONS"
            )

    @pytest.mark.unit
    def test_driver_transitions_cover_all_states(self) -> None:
        """כל DriverState מוגדר כמקור או יעד ב-DRIVER_TRANSITIONS"""
        keys = set(DRIVER_TRANSITIONS.keys())
        targets = self._all_target_states(DRIVER_TRANSITIONS)
        covered = keys | targets

        for state in DriverState:
            assert state in covered, (
                f"DriverState.{state.name} לא מכוסה ב-DRIVER_TRANSITIONS"
            )

    @pytest.mark.unit
    def test_admin_transitions_cover_all_states(self) -> None:
        """כל AdminState מוגדר כמקור או יעד ב-ADMIN_TRANSITIONS"""
        keys = set(ADMIN_TRANSITIONS.keys())
        targets = self._all_target_states(ADMIN_TRANSITIONS)
        covered = keys | targets

        for state in AdminState:
            assert state in covered, (
                f"AdminState.{state.name} לא מכוסה ב-ADMIN_TRANSITIONS"
            )

    @pytest.mark.unit
    def test_sender_menu_transitions_valid(self) -> None:
        """תפריט שולח מוגדר ב-SENDER_TRANSITIONS"""
        assert SenderState.MENU in SENDER_TRANSITIONS
        targets = SENDER_TRANSITIONS[SenderState.MENU]
        assert SenderState.PICKUP_CITY in targets
        assert SenderState.VIEW_DELIVERIES in targets

    @pytest.mark.unit
    def test_courier_menu_transitions_valid(self) -> None:
        """תפריט שליח מוגדר ב-COURIER_TRANSITIONS"""
        assert CourierState.MENU in COURIER_TRANSITIONS
        targets = COURIER_TRANSITIONS[CourierState.MENU]
        assert CourierState.VIEW_AVAILABLE in targets
        assert CourierState.VIEW_WALLET in targets

    @pytest.mark.unit
    def test_dispatcher_menu_transitions_valid(self) -> None:
        """תפריט סדרן מוגדר ב-DISPATCHER_TRANSITIONS"""
        assert DispatcherState.MENU in DISPATCHER_TRANSITIONS
        targets = DISPATCHER_TRANSITIONS[DispatcherState.MENU]
        assert DispatcherState.ADD_SHIPMENT_PICKUP_CITY in targets
        assert DispatcherState.VIEW_ACTIVE_SHIPMENTS in targets

    @pytest.mark.unit
    def test_station_owner_menu_transitions_valid(self) -> None:
        """תפריט בעל תחנה מוגדר ב-STATION_OWNER_TRANSITIONS"""
        assert StationOwnerState.MENU in STATION_OWNER_TRANSITIONS
        targets = STATION_OWNER_TRANSITIONS[StationOwnerState.MENU]
        assert StationOwnerState.MANAGE_OWNERS in targets
        assert StationOwnerState.VIEW_WALLET in targets

    @pytest.mark.unit
    def test_transitions_targets_are_same_role(self) -> None:
        """בדיקה שכל יעד ב-SENDER_TRANSITIONS הוא SenderState"""
        for _src, targets in SENDER_TRANSITIONS.items():
            for t in targets:
                assert isinstance(t, SenderState), (
                    f"יעד {t!r} ב-SENDER_TRANSITIONS אינו SenderState"
                )

    @pytest.mark.unit
    def test_courier_transitions_targets_same_role(self) -> None:
        for _src, targets in COURIER_TRANSITIONS.items():
            for t in targets:
                assert isinstance(t, CourierState)

    @pytest.mark.unit
    def test_dispatcher_transitions_targets_same_role(self) -> None:
        for _src, targets in DISPATCHER_TRANSITIONS.items():
            for t in targets:
                assert isinstance(t, DispatcherState)

    @pytest.mark.unit
    def test_station_owner_transitions_targets_same_role(self) -> None:
        for _src, targets in STATION_OWNER_TRANSITIONS.items():
            for t in targets:
                assert isinstance(t, StationOwnerState)

    @pytest.mark.unit
    def test_driver_transitions_targets_same_role(self) -> None:
        for _src, targets in DRIVER_TRANSITIONS.items():
            for t in targets:
                assert isinstance(t, DriverState)


# ============================================================================
# StateManager — שמירה ושחזור state
# ============================================================================


class TestStateManager:
    """בדיקות StateManager עם DB"""

    @pytest.mark.asyncio
    async def test_force_and_get_state_sender(self, db_session, user_factory) -> None:
        """force_state ו-get_current_state לשולח"""
        user = await user_factory(
            phone_number="+972501001001",
            role=UserRole.SENDER,
            platform="whatsapp",
        )
        manager = StateManager(db_session)
        await manager.force_state(user.id, "whatsapp", SenderState.MENU.value, context={})
        state = await manager.get_current_state(user.id, "whatsapp")
        assert state == SenderState.MENU.value

    @pytest.mark.asyncio
    async def test_force_and_get_state_courier(self, db_session, user_factory) -> None:
        """force_state ו-get_current_state לשליח"""
        user = await user_factory(
            phone_number="+972501001002",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="tg_courier_qa",
        )
        manager = StateManager(db_session)
        await manager.force_state(user.id, "telegram", CourierState.MENU.value, context={})
        state = await manager.get_current_state(user.id, "telegram")
        assert state == CourierState.MENU.value

    @pytest.mark.asyncio
    async def test_force_state_station_owner(self, db_session, user_factory) -> None:
        """force_state לבעל תחנה"""
        user = await user_factory(
            phone_number="+972501001003",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="tg_owner_qa",
        )
        manager = StateManager(db_session)
        await manager.force_state(user.id, "telegram", StationOwnerState.MENU.value, context={})
        state = await manager.get_current_state(user.id, "telegram")
        assert state == StationOwnerState.MENU.value

    @pytest.mark.asyncio
    async def test_force_state_driver(self, db_session, user_factory) -> None:
        """force_state לנהג"""
        user = await user_factory(
            phone_number="+972501001004",
            role=UserRole.DRIVER,
            platform="telegram",
            telegram_chat_id="tg_driver_qa",
        )
        manager = StateManager(db_session)
        await manager.force_state(user.id, "telegram", DriverState.MENU.value, context={})
        state = await manager.get_current_state(user.id, "telegram")
        assert state == DriverState.MENU.value

    @pytest.mark.asyncio
    async def test_valid_transition_sender(self, db_session, user_factory) -> None:
        """מעבר חוקי: SENDER.MENU → SENDER.DELIVERY.PICKUP_CITY"""
        user = await user_factory(
            phone_number="+972501001005",
            role=UserRole.SENDER,
            platform="whatsapp",
        )
        manager = StateManager(db_session)
        await manager.force_state(user.id, "whatsapp", SenderState.MENU.value, context={})

        success = await manager.transition_to(
            user.id, "whatsapp", SenderState.PICKUP_CITY.value
        )
        assert success is True
        state = await manager.get_current_state(user.id, "whatsapp")
        assert state == SenderState.PICKUP_CITY.value

    @pytest.mark.asyncio
    async def test_invalid_transition_sender(self, db_session, user_factory) -> None:
        """מעבר לא חוקי: SENDER.MENU → SENDER.DELIVERY.CONFIRM נדחה"""
        user = await user_factory(
            phone_number="+972501001006",
            role=UserRole.SENDER,
            platform="whatsapp",
        )
        manager = StateManager(db_session)
        await manager.force_state(user.id, "whatsapp", SenderState.MENU.value, context={})

        success = await manager.transition_to(
            user.id, "whatsapp", SenderState.DELIVERY_CONFIRM.value
        )
        assert success is False
        # State לא השתנה
        state = await manager.get_current_state(user.id, "whatsapp")
        assert state == SenderState.MENU.value

    @pytest.mark.asyncio
    async def test_valid_transition_courier(self, db_session, user_factory) -> None:
        """מעבר חוקי: COURIER.MENU → COURIER.VIEW_AVAILABLE"""
        user = await user_factory(
            phone_number="+972501001007",
            role=UserRole.COURIER,
            platform="whatsapp",
        )
        manager = StateManager(db_session)
        await manager.force_state(user.id, "whatsapp", CourierState.MENU.value, context={})

        success = await manager.transition_to(
            user.id, "whatsapp", CourierState.VIEW_AVAILABLE.value
        )
        assert success is True

    @pytest.mark.asyncio
    async def test_cross_role_transition_blocked(self, db_session, user_factory) -> None:
        """מעבר cross-role (SENDER.MENU → COURIER.MENU) נדחה"""
        user = await user_factory(
            phone_number="+972501001008",
            role=UserRole.SENDER,
            platform="whatsapp",
        )
        manager = StateManager(db_session)
        await manager.force_state(user.id, "whatsapp", SenderState.MENU.value, context={})

        success = await manager.transition_to(
            user.id, "whatsapp", CourierState.MENU.value
        )
        assert success is False

    @pytest.mark.asyncio
    async def test_context_persisted_on_force_state(self, db_session, user_factory) -> None:
        """context_data נשמר יחד עם force_state"""
        user = await user_factory(
            phone_number="+972501001009",
            role=UserRole.SENDER,
            platform="whatsapp",
        )
        manager = StateManager(db_session)
        await manager.force_state(
            user.id, "whatsapp", SenderState.MENU.value,
            context={"pickup_city": "תל אביב"}
        )
        ctx = await manager.get_context(user.id, "whatsapp")
        assert ctx.get("pickup_city") == "תל אביב"

    @pytest.mark.asyncio
    async def test_new_session_default_state(self, db_session, user_factory) -> None:
        """session חדש מתחיל ב-INITIAL"""
        user = await user_factory(
            phone_number="+972501001010",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="tg_new_session_qa",
        )
        manager = StateManager(db_session)
        state = await manager.get_current_state(user.id, "telegram")
        assert state == "INITIAL"


# ============================================================================
# יצירת משתמשים ב-DB — כל תפקיד
# ============================================================================


class TestUserCreationPerRole:
    """בדיקות DB — יצירת משתמש בכל תפקיד"""

    @pytest.mark.asyncio
    async def test_create_sender(self, user_factory) -> None:
        user = await user_factory(
            phone_number="+972502001001",
            role=UserRole.SENDER,
            platform="whatsapp",
        )
        assert user.role == UserRole.SENDER
        assert user.id is not None

    @pytest.mark.asyncio
    async def test_create_courier(self, user_factory) -> None:
        user = await user_factory(
            phone_number="+972502001002",
            role=UserRole.COURIER,
            platform="whatsapp",
            approval_status=ApprovalStatus.APPROVED,
        )
        assert user.role == UserRole.COURIER
        assert user.is_approved_courier is True

    @pytest.mark.asyncio
    async def test_create_admin(self, user_factory) -> None:
        user = await user_factory(
            phone_number="+972502001003",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="tg_admin_create_qa",
        )
        assert user.role == UserRole.ADMIN

    @pytest.mark.asyncio
    async def test_create_station_owner(self, user_factory) -> None:
        user = await user_factory(
            phone_number="+972502001004",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="tg_owner_create_qa",
        )
        assert user.role == UserRole.STATION_OWNER

    @pytest.mark.asyncio
    async def test_create_driver(self, user_factory) -> None:
        user = await user_factory(
            phone_number="+972502001005",
            role=UserRole.DRIVER,
            platform="telegram",
            telegram_chat_id="tg_driver_create_qa",
        )
        assert user.role == UserRole.DRIVER

    @pytest.mark.asyncio
    async def test_default_role_is_sender(self, db_session) -> None:
        """ברירת מחדל של role היא SENDER"""
        user = User(
            id=99999,
            phone_number="+972502001099",
            platform="whatsapp",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        assert user.role == UserRole.SENDER
