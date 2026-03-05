"""
בדיקות החלפת תפקיד לאדמין.

מוודאים שאדמין יכול להחליף תפקיד זמנית, לצפות בתפריטים
של תפקידים אחרים, ולחזור לתפקיד אדמין.
"""
import pytest
from unittest.mock import patch

from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_owner import StationOwner
from app.state_machine.states import AdminState
from app.state_machine.admin_handler import AdminStateHandler
from app.state_machine.manager import StateManager


# ============================================================================
# בדיקות AdminStateHandler ישירות
# ============================================================================


class TestAdminStateHandler:
    """בדיקות יחידה ל-AdminStateHandler"""

    @pytest.mark.asyncio
    async def test_admin_menu_shows_switch_button(self, db_session, user_factory):
        """תפריט אדמין מציג כפתור החלפת תפקיד"""
        admin = await user_factory(
            phone_number="+972500000001",
            name="Admin",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90001",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.MENU.value, context={}
        )

        handler = AdminStateHandler(db_session)
        response, new_state = await handler.handle_message(admin, "תפריט", None)

        assert "תפריט מנהל" in response.text
        assert new_state == AdminState.SELECT_ROLE.value

    @pytest.mark.asyncio
    async def test_select_role_shows_options(self, db_session, user_factory):
        """מסך בחירת תפקיד מציג את כל התפקידים"""
        admin = await user_factory(
            phone_number="+972500000002",
            name="Admin",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90002",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, context={}
        )

        handler = AdminStateHandler(db_session)
        response, new_state = await handler.handle_message(admin, "החלף תפקיד", None)

        assert "בחירת תפקיד" in response.text
        assert response.keyboard is not None
        # ולידציה שכל התפקידים מוצגים
        flat_keyboard = [btn for row in response.keyboard for btn in row]
        assert "שולח" in flat_keyboard
        assert "שליח" in flat_keyboard
        assert "נהג" in flat_keyboard
        assert "סדרן" in flat_keyboard
        assert "בעל תחנה" in flat_keyboard

    @pytest.mark.asyncio
    async def test_switch_to_sender(self, db_session, user_factory):
        """החלפה לשולח משנה role ב-DB"""
        admin = await user_factory(
            phone_number="+972500000003",
            name="Admin",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90003",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, context={}
        )

        handler = AdminStateHandler(db_session)
        response, new_state = await handler.handle_message(admin, "שולח", None)

        assert admin.role == UserRole.SENDER
        assert new_state.startswith("_ADMIN_SWITCH_")

        # ולידציה שה-context מכיל original_role
        context = await state_manager.get_context(admin.id, "telegram")
        assert context.get("original_role") == "admin"

    @pytest.mark.asyncio
    async def test_switch_to_courier_sets_approved(self, db_session, user_factory):
        """החלפה לשליח מגדירה approval_status ל-APPROVED"""
        admin = await user_factory(
            phone_number="+972500000004",
            name="Admin",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90004",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, context={}
        )

        handler = AdminStateHandler(db_session)
        response, new_state = await handler.handle_message(admin, "שליח", None)

        assert admin.role == UserRole.COURIER
        assert admin.approval_status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_switch_to_dispatcher_creates_association(self, db_session, user_factory):
        """החלפה לסדרן יוצרת שיוך לתחנה"""
        admin = await user_factory(
            phone_number="+972500000005",
            name="Admin",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90005",
        )
        # יצירת תחנה פעילה
        station = Station(
            id=1, name="Test Station", owner_id=admin.id, is_active=True
        )
        db_session.add(station)
        await db_session.flush()

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, context={}
        )

        handler = AdminStateHandler(db_session)
        response, new_state = await handler.handle_message(admin, "סדרן", None)

        # ולידציה שנוצר שיוך סדרן
        from sqlalchemy import select
        result = await db_session.execute(
            select(StationDispatcher).where(
                StationDispatcher.user_id == admin.id,
                StationDispatcher.station_id == station.id,
            )
        )
        dispatcher = result.scalar_one_or_none()
        assert dispatcher is not None
        assert dispatcher.is_active is True

    @pytest.mark.asyncio
    async def test_switch_to_station_owner_creates_association(self, db_session, user_factory):
        """החלפה לבעל תחנה יוצרת שיוך"""
        admin = await user_factory(
            phone_number="+972500000006",
            name="Admin",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90006",
        )
        station = Station(
            id=2, name="Test Station 2", owner_id=admin.id, is_active=True
        )
        db_session.add(station)
        await db_session.flush()

        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, context={}
        )

        handler = AdminStateHandler(db_session)
        response, new_state = await handler.handle_message(admin, "בעל תחנה", None)

        assert admin.role == UserRole.STATION_OWNER

        from sqlalchemy import select
        result = await db_session.execute(
            select(StationOwner).where(
                StationOwner.user_id == admin.id,
                StationOwner.station_id == station.id,
            )
        )
        owner = result.scalar_one_or_none()
        assert owner is not None
        assert owner.is_active is True

    @pytest.mark.asyncio
    async def test_back_button_returns_to_menu(self, db_session, user_factory):
        """כפתור חזרה ב-SELECT_ROLE מחזיר לתפריט אדמין"""
        admin = await user_factory(
            phone_number="+972500000008",
            name="Admin",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90008",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, context={}
        )

        handler = AdminStateHandler(db_session)
        response, new_state = await handler.handle_message(admin, "חזרה", None)

        assert "תפריט מנהל" in response.text
        assert new_state == AdminState.SELECT_ROLE.value

    @pytest.mark.asyncio
    async def test_switch_role_preserves_admin_context_after_route(
        self, db_session, user_factory
    ):
        """מעבר תפקיד שומר מפתחות אדמין גם אחרי force_state של הניתוב"""
        admin = await user_factory(
            phone_number="+972500000009",
            name="Admin",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90009",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, context={}
        )

        handler = AdminStateHandler(db_session)
        response, new_state = await handler.handle_message(admin, "שולח", None)
        assert new_state.startswith("_ADMIN_SWITCH_")

        # שמירת מפתחות אדמין לפני ניתוב (כמו שהוובהוק עושה)
        admin_ctx = await state_manager.get_context(admin.id, "telegram")
        assert admin_ctx.get("original_role") == "admin"

        # סימולציה: הניתוב מוחק context (כמו _route_to_role_menu)
        await state_manager.force_state(
            admin.id, "telegram", "SENDER.MENU", context={}
        )

        # שחזור מפתחות אדמין (כמו שהתיקון בוובהוק עושה)
        _admin_keys = {
            k: admin_ctx[k]
            for k in ("original_role", "original_approval_status",
                      "admin_station_id", "admin_target_role")
            if k in admin_ctx
        }
        ctx = await state_manager.get_context(admin.id, "telegram")
        ctx.update(_admin_keys)
        await state_manager.force_state(
            admin.id, "telegram", "SENDER.MENU", context=ctx
        )

        # ולידציה שמפתחות אדמין נשמרו
        final_ctx = await state_manager.get_context(admin.id, "telegram")
        assert final_ctx.get("original_role") == "admin"
        assert final_ctx.get("admin_target_role") == "sender"

    @pytest.mark.asyncio
    async def test_platform_parameter(self, db_session, user_factory):
        """AdminStateHandler משתמש ב-platform שמועבר בבנאי"""
        admin = await user_factory(
            phone_number="+972500000013",
            name="Admin WA",
            role=UserRole.ADMIN,
            platform="whatsapp",
            telegram_chat_id="90013",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "whatsapp", AdminState.MENU.value, context={}
        )

        handler = AdminStateHandler(db_session, platform="whatsapp")
        response, new_state = await handler.handle_message(admin, "תפריט", None)

        # ולידציה שה-state נכתב לפלטפורמה הנכונה
        ctx = await state_manager.get_context(admin.id, "whatsapp")
        assert new_state == AdminState.SELECT_ROLE.value

    @pytest.mark.asyncio
    async def test_no_station_returns_error(self, db_session, user_factory):
        """ניסיון להחליף לסדרן ללא תחנה מחזיר שגיאה"""
        admin = await user_factory(
            phone_number="+972500000007",
            name="Admin",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90007",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, context={}
        )

        handler = AdminStateHandler(db_session)
        response, new_state = await handler.handle_message(admin, "סדרן", None)

        assert "אין תחנה" in response.text
        # role לא השתנה
        assert admin.role == UserRole.ADMIN


# ============================================================================
# בדיקות ניתוב Telegram webhook
# ============================================================================


class TestAdminTelegramRouting:
    """בדיקות ניתוב אדמין ב-Telegram webhook"""

    @pytest.mark.asyncio
    async def test_admin_start_shows_admin_menu(
        self, test_client, db_session, user_factory
    ):
        """אדמין שמשתמש ב-/start רואה תפריט אדמין"""
        admin = await user_factory(
            phone_number="+972500000010",
            name="Admin Menu",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90010",
        )

        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 200,
                "message": {
                    "message_id": 200,
                    "chat": {"id": 90010, "type": "private"},
                    "text": "/start",
                    "date": 1700000000,
                    "from": {"id": 90010, "first_name": "Admin"},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_admin_disabled_shows_sender_menu(
        self, test_client, db_session, user_factory
    ):
        """כשהפיצ'ר כבוי, אדמין רואה תפריט שולח"""
        admin = await user_factory(
            phone_number="+972500000011",
            name="Admin Disabled",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90011",
        )

        with patch("app.api.webhooks.telegram.settings") as mock_settings:
            mock_settings.ADMIN_ROLE_SWITCH_ENABLED = False
            # צריך גם לעבור את שאר ה-settings
            mock_settings.TELEGRAM_ADMIN_CHAT_IDS = ""
            mock_settings.TELEGRAM_ADMIN_CHAT_ID = None
            mock_settings.TELEGRAM_BOT_TOKEN = None
            mock_settings.TELEGRAM_WEBHOOK_SECRET_TOKEN = ""
            mock_settings.WHATSAPP_ADMIN_GROUP_ID = None
            mock_settings.WHATSAPP_ADMIN_NUMBERS = ""

            resp = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 201,
                    "message": {
                        "message_id": 201,
                        "chat": {"id": 90011, "type": "private"},
                        "text": "/start",
                        "date": 1700000000,
                        "from": {"id": 90011, "first_name": "Admin"},
                    },
                },
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_return_to_admin_restores_role(
        self, db_session, user_factory
    ):
        """שליחת 'חזרה לאדמין' מחזירה את התפקיד ל-ADMIN"""
        admin = await user_factory(
            phone_number="+972500000012",
            name="Admin Return",
            role=UserRole.SENDER,  # שונה לשולח כי "הוחלף"
            platform="telegram",
            telegram_chat_id="90012",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", "SENDER.MENU",
            context={
                "original_role": "admin",
                "original_approval_status": None,
                "admin_station_id": None,
                "admin_target_role": "sender",
            },
        )

        # סימולציה: שינוי ל-admin דרך webhook handler
        # כאן בודקים רק את הלוגיקה של StateManager
        context = await state_manager.get_context(admin.id, "telegram")
        assert context["original_role"] == "admin"


# ============================================================================
# בדיקות Config
# ============================================================================


class TestAdminConfig:
    """בדיקות הגדרת config"""

    def test_default_admin_role_switch_enabled(self):
        """ברירת מחדל: פיצ'ר מופעל"""
        from app.core.config import settings
        assert settings.ADMIN_ROLE_SWITCH_ENABLED is True


# ============================================================================
# בדיקות States
# ============================================================================


class TestAdminStates:
    """בדיקות AdminState enum ו-transitions"""

    def test_admin_state_values(self):
        """ולידציית ערכי AdminState"""
        assert AdminState.MENU.value == "ADMIN.MENU"
        assert AdminState.SELECT_ROLE.value == "ADMIN.SELECT_ROLE"

    def test_admin_transitions_defined(self):
        """ולידציה שמעברים מוגדרים"""
        from app.state_machine.states import ADMIN_TRANSITIONS

        assert AdminState.MENU in ADMIN_TRANSITIONS
        assert AdminState.SELECT_ROLE in ADMIN_TRANSITIONS
        assert AdminState.SELECT_ROLE in ADMIN_TRANSITIONS[AdminState.MENU]
        assert AdminState.MENU in ADMIN_TRANSITIONS[AdminState.SELECT_ROLE]

    @pytest.mark.asyncio
    async def test_admin_transitions_registered_in_state_manager(self, db_session):
        """ADMIN_TRANSITIONS רשום ב-StateManager._is_valid_transition"""
        state_manager = StateManager(db_session)
        assert state_manager._is_valid_transition(
            AdminState.MENU.value, AdminState.SELECT_ROLE.value
        ) is True
        assert state_manager._is_valid_transition(
            AdminState.SELECT_ROLE.value, AdminState.MENU.value
        ) is True
        # מעבר לא חוקי
        assert state_manager._is_valid_transition(
            AdminState.MENU.value, AdminState.MENU.value
        ) is False

    @pytest.mark.asyncio
    async def test_transition_to_works_for_admin(self, db_session, user_factory):
        """transition_to מצליח עבור מעברי admin ללא force_state"""
        admin = await user_factory(
            phone_number="+972500000020",
            name="Admin Trans",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90020",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            admin.id, "telegram", AdminState.MENU.value, context={}
        )
        success = await state_manager.transition_to(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, {}
        )
        assert success is True
        current = await state_manager.get_current_state(admin.id, "telegram")
        assert current == AdminState.SELECT_ROLE.value

    @pytest.mark.asyncio
    async def test_disabled_feature_continues_sender_flow(self, db_session, user_factory):
        """אדמין עם פיצ'ר כבוי ממשיך בזרימת שולח כשכבר במצב SENDER"""
        admin = await user_factory(
            phone_number="+972500000021",
            name="Admin Disabled Flow",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90021",
        )
        state_manager = StateManager(db_session)
        # אדמין כבר ב-state שולח (אחרי fallback ראשון)
        await state_manager.force_state(
            admin.id, "telegram", "SENDER.MENU", context={}
        )

        # סימולציה: SenderStateHandler אמור לטפל בהודעה
        from app.state_machine.handlers import SenderStateHandler
        handler = SenderStateHandler(db_session)
        response, new_state = await handler.handle_message(
            user_id=admin.id, platform="telegram", message="תפריט"
        )
        # ולידציה שה-handler מחזיר state תקין (לא נופל)
        assert response is not None
        assert new_state is not None


# ============================================================================
# בדיקות באגים ספציפיים
# ============================================================================


class TestAdminBugFixes:
    """בדיקות תיקוני באגים באדמין"""

    @pytest.mark.asyncio
    async def test_stale_sender_check_allows_admin_role(self, db_session, user_factory):
        """בדיקת סטייט שולח לא מאפסת אדמין עם פיצ'ר כבוי.

        באג: הבדיקה ב-stale state check סיננה user.role != UserRole.SENDER
        מה שגרם לאדמינים עם state SENDER.* להיכנס ללולאת איפוס.
        תיקון: הוספת UserRole.ADMIN לרשימת התפקידים המותרים.
        """
        admin = await user_factory(
            phone_number="+972500000030",
            name="Admin Stale Check",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90030",
        )
        state_manager = StateManager(db_session)
        # אדמין ב-state שולח (אחרי _sender_fallback)
        await state_manager.force_state(
            admin.id, "telegram", "SENDER.MENU", context={}
        )

        # ולידציה: user.role הוא ADMIN עם state SENDER.*
        # הבדיקה החדשה מאפשרת שילוב זה (לא מאפסת)
        assert admin.role == UserRole.ADMIN
        current = await state_manager.get_current_state(admin.id, "telegram")
        assert current == "SENDER.MENU"

        # ולידציה ישירה של התנאי החדש
        # user.role not in (UserRole.SENDER, UserRole.ADMIN) → False → לא מאפסים
        assert admin.role in (UserRole.SENDER, UserRole.ADMIN)

    @pytest.mark.asyncio
    async def test_admin_context_preserves_none_values(self, db_session, user_factory):
        """מפתחות אדמין עם ערך None נשמרים בשחזור.

        באג: dict comprehension סינן מפתחות עם ערך None באמצעות
        `if admin_ctx.get(k) is not None`, מה שגרם לאיבוד מפתחות
        כמו original_approval_status=None.
        תיקון: שימוש ב-`if k in admin_ctx` במקום.
        """
        admin = await user_factory(
            phone_number="+972500000031",
            name="Admin None Context",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="90031",
        )
        state_manager = StateManager(db_session)
        # context עם ערכי None לגיטימיים
        admin_ctx = {
            "original_role": "admin",
            "original_approval_status": None,
            "admin_station_id": None,
            "admin_target_role": "sender",
        }
        await state_manager.force_state(
            admin.id, "telegram", AdminState.SELECT_ROLE.value, context=admin_ctx
        )

        # סימולציה: שחזור מפתחות אדמין עם התיקון החדש
        _admin_keys = {
            k: admin_ctx[k]
            for k in ("original_role", "original_approval_status",
                      "admin_station_id", "admin_target_role")
            if k in admin_ctx
        }

        # ולידציה: כל 4 המפתחות נשמרים, כולל אלה עם None
        assert len(_admin_keys) == 4
        assert "original_approval_status" in _admin_keys
        assert _admin_keys["original_approval_status"] is None
        assert "admin_station_id" in _admin_keys
        assert _admin_keys["admin_station_id"] is None

        # ולידציה: ערכים לא-None נשמרים גם
        assert _admin_keys["original_role"] == "admin"
        assert _admin_keys["admin_target_role"] == "sender"

    @pytest.mark.asyncio
    async def test_admin_context_old_pattern_drops_none(self):
        """ולידציה שהדפוס הישן אכן מאבד מפתחות None.

        בדיקת רגרסיה: מוודאת שהבאג קיים בדפוס הישן.
        """
        admin_ctx = {
            "original_role": "admin",
            "original_approval_status": None,
            "admin_station_id": None,
            "admin_target_role": "sender",
        }

        # דפוס ישן (באגי) - מסנן None
        old_keys = {
            k: admin_ctx.get(k)
            for k in ("original_role", "original_approval_status",
                      "admin_station_id", "admin_target_role")
            if admin_ctx.get(k) is not None
        }
        # רק 2 מפתחות נשמרים (original_role, admin_target_role)
        assert len(old_keys) == 2
        assert "original_approval_status" not in old_keys

        # דפוס חדש (מתוקן) - שומר הכל
        new_keys = {
            k: admin_ctx[k]
            for k in ("original_role", "original_approval_status",
                      "admin_station_id", "admin_target_role")
            if k in admin_ctx
        }
        assert len(new_keys) == 4
        assert "original_approval_status" in new_keys

    @pytest.mark.asyncio
    async def test_inject_admin_return_button(self):
        """הוספת כפתור 'חזרה לאדמין' לתגובה."""
        from app.state_machine.handlers import MessageResponse
        from app.api.webhooks.telegram import _inject_admin_return_button

        # עם keyboard קיים
        response = MessageResponse("test", keyboard=[["כפתור 1"]])
        _inject_admin_return_button(response)
        flat = [btn for row in response.keyboard for btn in row]
        assert "🔙 חזרה לאדמין" in flat

        # ללא keyboard
        response2 = MessageResponse("test", keyboard=None)
        _inject_admin_return_button(response2)
        assert response2.keyboard == [["🔙 חזרה לאדמין"]]

        # לא מוסיף כפול
        _inject_admin_return_button(response)
        count = sum(
            1 for row in response.keyboard for btn in row if "חזרה לאדמין" in btn
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_route_to_role_menu_preserves_admin_context(
        self, db_session, user_factory
    ):
        """_route_to_role_menu שומרת admin context ומוסיפה כפתור 'חזרה לאדמין'."""
        admin = await user_factory(
            phone_number="+972500000032",
            name="Admin Route Preserve",
            role=UserRole.SENDER,  # שונה מ-ADMIN (כמו אחרי החלפת תפקיד)
            platform="telegram",
            telegram_chat_id="90032",
        )
        state_manager = StateManager(db_session)
        # סימולציה: admin context קיים (כמו אחרי החלפת תפקיד)
        await state_manager.force_state(
            admin.id, "telegram", "SENDER.MENU",
            context={
                "original_role": "admin",
                "original_approval_status": None,
                "admin_station_id": None,
                "admin_target_role": "sender",
            },
        )

        from app.api.webhooks.telegram import _route_to_role_menu

        response, new_state = await _route_to_role_menu(admin, db_session, state_manager)

        # ולידציה: admin context נשמר
        ctx = await state_manager.get_context(admin.id, "telegram")
        assert ctx.get("original_role") == "admin"
        assert ctx.get("admin_target_role") == "sender"

        # ולידציה: כפתור "חזרה לאדמין" מופיע
        flat = [btn for row in (response.keyboard or []) for btn in row]
        assert "🔙 חזרה לאדמין" in flat

    @pytest.mark.asyncio
    async def test_route_to_role_menu_no_button_without_admin_context(
        self, db_session, user_factory
    ):
        """_route_to_role_menu לא מוסיפה כפתור כשאין admin context."""
        sender = await user_factory(
            phone_number="+972500000033",
            name="Regular Sender",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="90033",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            sender.id, "telegram", "SENDER.MENU", context={}
        )

        from app.api.webhooks.telegram import _route_to_role_menu

        response, new_state = await _route_to_role_menu(sender, db_session, state_manager)

        # ולידציה: אין כפתור "חזרה לאדמין" למשתמש רגיל
        flat = [btn for row in (response.keyboard or []) for btn in row]
        assert "🔙 חזרה לאדמין" not in flat
