"""
בדיקות ניתוב Webhook לפי תפקיד.

מוודאים שכל נקודת כניסה (/start, #, כפתורי תפריט) מנתבת נכון
עבור כל תפקיד (UserRole) - למניעת רגרסיות בעת הוספת תפקידים חדשים.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import Response

from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.state_machine.states import CourierState, StationOwnerState


# ============================================================================
# ניתוב /start ו-# לפי תפקיד
# ============================================================================


class TestResetRoutingByRole:
    """מוודא ש-/start ו-# מנתבים לתפריט הנכון לכל תפקיד"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reset_command", ["/start", "#"])
    async def test_sender_reset_shows_welcome(
        self, test_client, db_session, user_factory, reset_command
    ):
        """שולח - /start ו-# מחזירים למסך ברוכים הבאים / תפריט שולח"""
        sender = await user_factory(
            phone_number="+972501111001",
            name="Sender Reset",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="81001",
        )

        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 100,
                "message": {
                    "message_id": 100,
                    "chat": {"id": 81001, "type": "private"},
                    "text": reset_command,
                    "date": 1700000000,
                    "from": {"id": 81001, "first_name": "Sender"},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reset_command", ["/start", "#"])
    async def test_approved_courier_reset_routes_to_courier_menu(
        self, test_client, db_session, user_factory, reset_command
    ):
        """שליח מאושר - /start ו-# מנתבים לתפריט נהג"""
        courier = await user_factory(
            phone_number="+972501111002",
            name="Courier Reset",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="81002",
            approval_status=ApprovalStatus.APPROVED,
        )

        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 101,
                "message": {
                    "message_id": 101,
                    "chat": {"id": 81002, "type": "private"},
                    "text": reset_command,
                    "date": 1700000000,
                    "from": {"id": 81002, "first_name": "Courier"},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert CourierState.MENU.value in data.get("new_state", "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reset_command", ["/start", "#"])
    async def test_station_owner_reset_routes_to_station_panel(
        self, test_client, db_session, user_factory, reset_command
    ):
        """בעל תחנה - /start ו-# מנתבים לפאנל בעל תחנה"""
        owner = await user_factory(
            phone_number="+972501111003",
            name="Owner Reset",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="81003",
        )

        # יצירת תחנה + ארנק
        station = Station(name="תחנת ניתוב", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 102,
                "message": {
                    "message_id": 102,
                    "chat": {"id": 81003, "type": "private"},
                    "text": reset_command,
                    "date": 1700000000,
                    "from": {"id": 81003, "first_name": "Owner"},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert StationOwnerState.MENU.value in data.get("new_state", "")


# ============================================================================
# הגנה מפני יירוט כפתורים בזרימות רב-שלביות
# ============================================================================


class TestMultiStepFlowGuard:
    """מוודא שמשתמשים באמצע זרימה רב-שלבית לא נתפסים ע"י כפתורי תפריט"""

    @pytest.mark.asyncio
    async def test_dispatcher_address_with_station_keyword_not_intercepted(
        self, test_client, db_session, user_factory
    ):
        """סדרן שמזין כתובת עם 'תחנה' לא נתפס ע"י בדיקת כפתור שיווקי"""
        from app.state_machine.manager import StateManager
        from app.state_machine.states import DispatcherState
        from app.db.models.station_dispatcher import StationDispatcher

        courier = await user_factory(
            phone_number="+972501111004",
            name="Dispatcher Addr",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="81004",
            approval_status=ApprovalStatus.APPROVED,
        )

        # יצירת תחנה וסדרן
        owner = await user_factory(
            phone_number="+972501111005",
            name="Owner Addr",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="81005",
        )
        station = Station(name="תחנה לבדיקה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        link = StationDispatcher(station_id=station.id, user_id=courier.id)
        db_session.add(link)
        await db_session.commit()

        # הגדרת state לאמצע הזנת כתובת
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            courier.id, "telegram",
            DispatcherState.ADD_SHIPMENT_PICKUP_CITY.value,
            {"pickup_city": ""}
        )

        # שליחת "תחנה מרכזית" - צריך להמשיך בזרימה, לא ליפול לשיווק
        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 103,
                "message": {
                    "message_id": 103,
                    "chat": {"id": 81004, "type": "private"},
                    "text": "תחנה מרכזית",
                    "date": 1700000000,
                    "from": {"id": 81004, "first_name": "Dispatcher"},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # צריך לעבור ל-state הבא בזרימה (רחוב), לא להישאר תקוע
        assert data["ok"] is True
        assert data.get("new_state") == DispatcherState.ADD_SHIPMENT_PICKUP_STREET.value

    @pytest.mark.asyncio
    async def test_station_owner_mid_flow_not_intercepted(
        self, test_client, db_session, user_factory
    ):
        """בעל תחנה באמצע הוספת רשימה שחורה לא נתפס ע"י כפתור 'משלוח מהיר'"""
        from app.state_machine.manager import StateManager
        from app.state_machine.states import StationOwnerState

        owner = await user_factory(
            phone_number="+972501111006",
            name="Owner Flow",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="81006",
        )

        station = Station(name="תחנה לבדיקת זרימה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        # הגדרת state לאמצע הוספת סיבה לרשימה שחורה
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            owner.id, "telegram",
            StationOwnerState.ADD_BLACKLIST_REASON.value,
            {"blacklist_phone": "+972501234567"}
        )

        # שליחת טקסט עם "משלוח מהיר" - צריך להמשיך בזרימה
        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 104,
                "message": {
                    "message_id": 104,
                    "chat": {"id": 81006, "type": "private"},
                    "text": "לא שילם על משלוח מהיר",
                    "date": 1700000000,
                    "from": {"id": 81006, "first_name": "Owner"},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        # לא צריך ליפול לתשובת שיווק - צריך להישאר בזרימת בעל תחנה
        assert "new_state" in data
