"""
בדיקות לשלב 1 (הודעת ברוכים הבאים ותפריט ראשי) ושלב 2 (רישום נהג KYC).

שלב 1: מוודא שהודעת הפתיחה כוללת 4 כפתורים וטקסט מעודכן.
שלב 2: מוודא את כל זרימת ה-KYC: שם -> מסמך -> סלפי -> קטגוריית רכב -> תמונת רכב -> תקנון.
כולל בדיקות לתהליך אישור/דחייה של שליחים.
"""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient

from app.state_machine.states import CourierState, COURIER_TRANSITIONS
from app.state_machine.handlers import CourierStateHandler, MessageResponse
from app.state_machine.manager import StateManager
from app.db.models.user import User, UserRole, ApprovalStatus
from app.domain.services.courier_approval_service import CourierApprovalService
from app.core.config import settings


# ============================================================================
# שלב 1 - הודעת ברוכים הבאים ותפריט ראשי
# ============================================================================


class TestStage1WelcomeMessage:
    """בדיקות להודעת ברוכים הבאים החדשה [שלב 1]"""

    @pytest.mark.asyncio
    async def test_telegram_new_user_gets_welcome_message(self, test_client: AsyncClient):
        """משתמש חדש בטלגרם מקבל הודעת ברוכים הבאים"""
        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 55555, "type": "private"},
                    "text": "שלום",
                    "date": 1700000000,
                    "from": {"id": 55555, "first_name": "Test"},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("new_user") is True

    @pytest.mark.asyncio
    async def test_whatsapp_new_user_gets_welcome_message(
        self, test_client: AsyncClient, mock_whatsapp_gateway
    ):
        """משתמש חדש בוואטסאפ מקבל הודעת ברוכים הבאים"""
        resp = await test_client.post(
            "/api/whatsapp/webhook",
            json={
                "messages": [
                    {
                        "from_number": "972509999999@c.us",
                        "sender_id": "972509999999@lid",
                        "reply_to": "972509999999@c.us",
                        "message_id": "m1",
                        "text": "שלום",
                        "timestamp": 1700000000,
                    }
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 1
        assert data["responses"][0]["new_user"] is True

    @pytest.mark.asyncio
    async def test_telegram_join_as_driver_button(self, test_client: AsyncClient):
        """לחיצה על כפתור 'הצטרפות למנוי וקבלת משלוחים' מתחילה רישום שליח"""
        # יצירת משתמש קיים קודם
        await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 66666, "type": "private"},
                    "text": "שלום",
                    "date": 1700000000,
                    "from": {"id": 66666, "first_name": "Driver"},
                },
            },
        )

        # לחיצה על כפתור הצטרפות
        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 2,
                "callback_query": {
                    "id": "cb-join",
                    "data": "🚚 הצטרפות למנוי וקבלת משלוחים",
                    "from": {"id": 66666, "first_name": "Driver"},
                    "message": {
                        "message_id": 2,
                        "chat": {"id": 66666, "type": "private"},
                        "text": "",
                        "date": 1700000001,
                    },
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # הנהג צריך לעבור לשלב איסוף שם
        assert data.get("new_state") == CourierState.REGISTER_COLLECT_NAME.value

    @pytest.mark.asyncio
    async def test_telegram_quick_shipment_button(self, test_client: AsyncClient):
        """לחיצה על כפתור 'העלאת משלוח מהיר' מחזירה הודעה עם קישור"""
        # יצירת משתמש
        await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 77777, "type": "private"},
                    "text": "שלום",
                    "date": 1700000000,
                    "from": {"id": 77777, "first_name": "Sender"},
                },
            },
        )

        # לחיצה על כפתור משלוח מהיר
        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 2,
                "callback_query": {
                    "id": "cb-quick",
                    "data": "📦 העלאת משלוח מהיר",
                    "from": {"id": 77777, "first_name": "Sender"},
                    "message": {
                        "message_id": 2,
                        "chat": {"id": 77777, "type": "private"},
                        "text": "",
                        "date": 1700000001,
                    },
                },
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_telegram_station_button(self, test_client: AsyncClient):
        """לחיצה על כפתור 'הצטרפות כתחנה' מחזירה הודעה שיווקית"""
        await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 88888, "type": "private"},
                    "text": "שלום",
                    "date": 1700000000,
                    "from": {"id": 88888, "first_name": "Station"},
                },
            },
        )

        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 2,
                "callback_query": {
                    "id": "cb-station",
                    "data": "🏪 הצטרפות כתחנה",
                    "from": {"id": 88888, "first_name": "Station"},
                    "message": {
                        "message_id": 2,
                        "chat": {"id": 88888, "type": "private"},
                        "text": "",
                        "date": 1700000001,
                    },
                },
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_telegram_contact_admin_button(self, test_client: AsyncClient):
        """לחיצה על כפתור 'פנייה לניהול' מחזירה קישור למנהל"""
        await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 99999, "type": "private"},
                    "text": "שלום",
                    "date": 1700000000,
                    "from": {"id": 99999, "first_name": "Contact"},
                },
            },
        )

        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 2,
                "callback_query": {
                    "id": "cb-admin",
                    "data": "📞 פנייה לניהול",
                    "from": {"id": 99999, "first_name": "Contact"},
                    "message": {
                        "message_id": 2,
                        "chat": {"id": 99999, "type": "private"},
                        "text": "",
                        "date": 1700000001,
                    },
                },
            },
        )
        assert resp.status_code == 200


# ============================================================================
# שלב 2 - רישום נהג KYC - State Machine transitions
# ============================================================================


class TestStage2KYCStateTransitions:
    """בדיקת מעברי מצבים בזרימת KYC [שלב 2]"""

    @pytest.mark.unit
    def test_kyc_states_exist(self):
        """מוודא שכל ה-states החדשים קיימים"""
        assert hasattr(CourierState, "REGISTER_COLLECT_SELFIE")
        assert hasattr(CourierState, "REGISTER_COLLECT_VEHICLE_CATEGORY")
        assert hasattr(CourierState, "REGISTER_COLLECT_VEHICLE_PHOTO")

    @pytest.mark.unit
    def test_kyc_state_transitions_defined(self):
        """מוודא שכל מעברי ה-KYC מוגדרים"""
        # שם -> מסמך
        assert CourierState.REGISTER_COLLECT_DOCUMENT in COURIER_TRANSITIONS[CourierState.REGISTER_COLLECT_NAME]
        # מסמך -> סלפי
        assert CourierState.REGISTER_COLLECT_SELFIE in COURIER_TRANSITIONS[CourierState.REGISTER_COLLECT_DOCUMENT]
        # סלפי -> קטגוריית רכב
        assert CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY in COURIER_TRANSITIONS[CourierState.REGISTER_COLLECT_SELFIE]
        # קטגוריית רכב -> תמונת רכב
        assert CourierState.REGISTER_COLLECT_VEHICLE_PHOTO in COURIER_TRANSITIONS[CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY]
        # תמונת רכב -> תקנון
        assert CourierState.REGISTER_TERMS in COURIER_TRANSITIONS[CourierState.REGISTER_COLLECT_VEHICLE_PHOTO]
        # תקנון -> ממתין לאישור
        assert CourierState.PENDING_APPROVAL in COURIER_TRANSITIONS[CourierState.REGISTER_TERMS]

    @pytest.mark.unit
    def test_full_kyc_flow_chain(self):
        """מוודא שכל שרשרת ה-KYC רציפה מתחילה ועד סוף"""
        flow = [
            CourierState.INITIAL,
            CourierState.REGISTER_COLLECT_NAME,
            CourierState.REGISTER_COLLECT_DOCUMENT,
            CourierState.REGISTER_COLLECT_SELFIE,
            CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY,
            CourierState.REGISTER_COLLECT_VEHICLE_PHOTO,
            CourierState.REGISTER_TERMS,
            CourierState.PENDING_APPROVAL,
        ]
        for i in range(len(flow) - 1):
            assert flow[i + 1] in COURIER_TRANSITIONS[flow[i]], (
                f"Missing transition: {flow[i].value} -> {flow[i + 1].value}"
            )


# ============================================================================
# שלב 2 - רישום נהג KYC - Handlers
# ============================================================================


class TestStage2KYCHandlers:
    """בדיקות ל-handlers של זרימת KYC [שלב 2]"""

    @pytest.mark.asyncio
    async def test_initial_shows_name_prompt(self, db_session, user_factory):
        """שלב ראשוני - מבקש שם מלא"""
        user = await user_factory(
            phone_number="tg:40001",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40001",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "start", None)
        assert new_state == CourierState.REGISTER_COLLECT_NAME.value
        assert "שם מלא" in response.text or "שמך המלא" in response.text

    @pytest.mark.asyncio
    async def test_collect_name_saves_and_advances(self, db_session, user_factory):
        """שלב א' - שם נשמר ומעבר לשלב מסמך"""
        user = await user_factory(
            phone_number="tg:40002",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40002",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")

        # מעבר לשלב שם
        await handler.handle_message(user, "start", None)

        # שליחת שם
        response, new_state = await handler.handle_message(user, "ישראל ישראלי", None)
        assert new_state == CourierState.REGISTER_COLLECT_DOCUMENT.value
        assert user.full_name == "ישראל ישראלי"

    @pytest.mark.asyncio
    async def test_collect_name_rejects_short(self, db_session, user_factory):
        """שלב א' - שם קצר מדי נדחה"""
        user = await user_factory(
            phone_number="tg:40003",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40003",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)

        response, new_state = await handler.handle_message(user, "א", None)
        assert new_state == CourierState.REGISTER_COLLECT_NAME.value
        assert "קצר" in response.text

    @pytest.mark.asyncio
    async def test_collect_document_requires_photo(self, db_session, user_factory):
        """שלב ב' - מסמך חייב תמונה"""
        user = await user_factory(
            phone_number="tg:40004",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40004",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)

        # שליחת טקסט במקום תמונה
        response, new_state = await handler.handle_message(user, "שלחתי", None)
        assert new_state == CourierState.REGISTER_COLLECT_DOCUMENT.value
        assert "תמונה" in response.text

    @pytest.mark.asyncio
    async def test_collect_document_advances_to_selfie(self, db_session, user_factory):
        """שלב ב' - מסמך מתקבל ומעבר לסלפי"""
        user = await user_factory(
            phone_number="tg:40005",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40005",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)

        # שליחת תמונה
        response, new_state = await handler.handle_message(user, "", "doc_file_123")
        assert new_state == CourierState.REGISTER_COLLECT_SELFIE.value
        assert "סלפי" in response.text

    @pytest.mark.asyncio
    async def test_collect_selfie_requires_photo(self, db_session, user_factory):
        """שלב ג' - סלפי חייב תמונה"""
        user = await user_factory(
            phone_number="tg:40006",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40006",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)
        await handler.handle_message(user, "", "doc_file_123")

        # שליחת טקסט במקום תמונה
        response, new_state = await handler.handle_message(user, "הנה", None)
        assert new_state == CourierState.REGISTER_COLLECT_SELFIE.value
        assert "תמונה" in response.text or "סלפי" in response.text

    @pytest.mark.asyncio
    async def test_collect_selfie_advances_to_vehicle_category(self, db_session, user_factory):
        """שלב ג' - סלפי מתקבל ומעבר לקטגוריית רכב"""
        user = await user_factory(
            phone_number="tg:40007",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40007",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)
        await handler.handle_message(user, "", "doc_file_123")

        # שליחת סלפי
        response, new_state = await handler.handle_message(user, "", "selfie_file_456")
        assert new_state == CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value
        assert "רכב" in response.text
        assert user.selfie_file_id == "selfie_file_456"

    @pytest.mark.asyncio
    async def test_collect_vehicle_category_options(self, db_session, user_factory):
        """שלב ד' - בחירת קטגוריית רכב עם כל האפשרויות"""
        user = await user_factory(
            phone_number="tg:40008",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40008",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)
        await handler.handle_message(user, "", "doc_file_123")
        await handler.handle_message(user, "", "selfie_file_456")

        # בדיקה שכל קטגוריה מתקבלת
        for text, expected_category in [
            ("🚗 רכב 4 מקומות", "car_4"),
            ("🚐 7 מקומות", "car_7"),
            ("🛻 טנדר", "pickup_truck"),
            ("🏍️ אופנוע", "motorcycle"),
        ]:
            # מאפסים את המצב לבחירת רכב
            state_manager = StateManager(db_session)
            await state_manager.force_state(
                user.id, "telegram",
                CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value,
                {}
            )
            response, new_state = await handler.handle_message(user, text, None)
            assert new_state == CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value, (
                f"Failed for category: {text}"
            )
            assert user.vehicle_category == expected_category

    @pytest.mark.asyncio
    async def test_collect_vehicle_category_rejects_invalid(self, db_session, user_factory):
        """שלב ד' - קטגוריה לא חוקית נדחית"""
        user = await user_factory(
            phone_number="tg:40009",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40009",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)
        await handler.handle_message(user, "", "doc_file_123")
        await handler.handle_message(user, "", "selfie_file_456")

        # שליחת טקסט לא חוקי
        response, new_state = await handler.handle_message(user, "מטוס", None)
        assert new_state == CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value
        assert "אפשרויות" in response.text or "בחר" in response.text

    @pytest.mark.asyncio
    async def test_collect_vehicle_photo_requires_photo(self, db_session, user_factory):
        """שלב ה' - תמונת רכב חייבת תמונה"""
        user = await user_factory(
            phone_number="tg:40010",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40010",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)
        await handler.handle_message(user, "", "doc_file_123")
        await handler.handle_message(user, "", "selfie_file_456")
        await handler.handle_message(user, "🚗 רכב 4 מקומות", None)

        # שליחת טקסט במקום תמונה
        response, new_state = await handler.handle_message(user, "הנה הרכב", None)
        assert new_state == CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value
        assert "תמונה" in response.text

    @pytest.mark.asyncio
    async def test_collect_vehicle_photo_advances_to_terms(self, db_session, user_factory):
        """שלב ה' - תמונת רכב מתקבלת ומעבר לתקנון"""
        user = await user_factory(
            phone_number="tg:40011",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40011",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)
        await handler.handle_message(user, "", "doc_file_123")
        await handler.handle_message(user, "", "selfie_file_456")
        await handler.handle_message(user, "🚗 רכב 4 מקומות", None)

        # שליחת תמונת רכב
        response, new_state = await handler.handle_message(user, "", "vehicle_file_789")
        assert new_state == CourierState.REGISTER_TERMS.value
        assert "תקנון" in response.text
        assert user.vehicle_photo_file_id == "vehicle_file_789"

    @pytest.mark.asyncio
    async def test_terms_acceptance_completes_registration(self, db_session, user_factory):
        """שלב ו' - אישור תקנון משלים את הרישום"""
        user = await user_factory(
            phone_number="tg:40012",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40012",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)
        await handler.handle_message(user, "", "doc_file_123")
        await handler.handle_message(user, "", "selfie_file_456")
        await handler.handle_message(user, "🚗 רכב 4 מקומות", None)
        await handler.handle_message(user, "", "vehicle_file_789")

        # אישור תקנון
        response, new_state = await handler.handle_message(user, "קראתי ואני מאשר ✅", None)
        assert new_state == CourierState.PENDING_APPROVAL.value
        assert "הושלם" in response.text
        assert user.approval_status == ApprovalStatus.PENDING
        assert user.terms_accepted_at is not None
        assert user.id_document_url == "doc_file_123"

    @pytest.mark.asyncio
    async def test_terms_rejection_stays_on_terms(self, db_session, user_factory):
        """שלב ו' - ללא אישור תקנון, נשאר באותו שלב"""
        user = await user_factory(
            phone_number="tg:40013",
            name="Test",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="40013",
            approval_status=None,
        )
        handler = CourierStateHandler(db_session, platform="telegram")
        await handler.handle_message(user, "start", None)
        await handler.handle_message(user, "ישראל ישראלי", None)
        await handler.handle_message(user, "", "doc_file_123")
        await handler.handle_message(user, "", "selfie_file_456")
        await handler.handle_message(user, "🚗 רכב 4 מקומות", None)
        await handler.handle_message(user, "", "vehicle_file_789")

        # ניסיון ללא אישור
        response, new_state = await handler.handle_message(user, "לא מסכים", None)
        assert new_state == CourierState.REGISTER_TERMS.value


# ============================================================================
# שלב 2 - זרימת KYC מלאה דרך webhook
# ============================================================================


class TestStage2KYCFullWebhookFlow:
    """בדיקת זרימת KYC מלאה דרך WhatsApp webhook [שלב 2]"""

    @pytest.mark.asyncio
    async def test_whatsapp_full_kyc_flow(
        self, test_client: AsyncClient, mock_whatsapp_gateway
    ):
        """זרימת KYC מלאה: הצטרפות -> שם -> מסמך -> סלפי -> רכב -> תמונה -> תקנון"""
        sender_id = "972508888888@lid"
        reply_to = "972508888888@c.us"
        msg_counter = [0]

        async def post(text: str, media_url: str = None, media_type: str = None) -> dict:
            msg_counter[0] += 1
            msg = {
                "from_number": reply_to,
                "sender_id": sender_id,
                "reply_to": reply_to,
                "message_id": f"m-kyc-{msg_counter[0]}",
                "text": text,
                "timestamp": 1700000000,
            }
            if media_url:
                msg["media_url"] = media_url
                msg["media_type"] = media_type or "image"
            r = await test_client.post(
                "/api/whatsapp/webhook",
                json={"messages": [msg]},
            )
            assert r.status_code == 200
            return r.json()

        # 1. יצירת משתמש חדש
        res = await post("שלום")
        assert res["responses"][0]["new_user"] is True

        # 2. לחיצה על "הצטרפות למנוי" -> תחילת KYC
        res = await post("🚚 הצטרפות למנוי וקבלת משלוחים")
        assert res["responses"][0]["new_state"] == CourierState.REGISTER_COLLECT_NAME.value

        # 3. שליחת שם
        res = await post("דוד כהן")
        assert res["responses"][0]["new_state"] == CourierState.REGISTER_COLLECT_DOCUMENT.value

        # 4. שליחת מסמך (תמונה)
        res = await post("", media_url="http://example.com/id.jpg", media_type="image")
        assert res["responses"][0]["new_state"] == CourierState.REGISTER_COLLECT_SELFIE.value

        # 5. שליחת סלפי (תמונה)
        res = await post("", media_url="http://example.com/selfie.jpg", media_type="image")
        assert res["responses"][0]["new_state"] == CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value

        # 6. בחירת קטגוריית רכב
        res = await post("🛻 טנדר")
        assert res["responses"][0]["new_state"] == CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value

        # 7. שליחת תמונת רכב
        res = await post("", media_url="http://example.com/car.jpg", media_type="image")
        assert res["responses"][0]["new_state"] == CourierState.REGISTER_TERMS.value

        # 8. אישור תקנון
        res = await post("קראתי ואני מאשר ✅")
        assert res["responses"][0]["new_state"] == CourierState.PENDING_APPROVAL.value


# ============================================================================
# שלב 2 - מודל User - שדות חדשים
# ============================================================================


class TestStage2UserModel:
    """בדיקות לשדות החדשים במודל User"""

    @pytest.mark.asyncio
    async def test_user_has_new_kyc_fields(self, db_session, user_factory):
        """מוודא שמודל User כולל את השדות החדשים"""
        user = await user_factory(
            phone_number="tg:50001",
            name="Test KYC",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="50001",
        )
        # השדות צריכים להיות None כברירת מחדל
        assert user.selfie_file_id is None
        assert user.vehicle_category is None
        assert user.vehicle_photo_file_id is None

    @pytest.mark.asyncio
    async def test_user_kyc_fields_persist(self, db_session, user_factory):
        """מוודא שהשדות החדשים נשמרים ב-DB"""
        user = await user_factory(
            phone_number="tg:50002",
            name="Test Persist",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="50002",
        )
        user.selfie_file_id = "selfie_abc"
        user.vehicle_category = "car_4"
        user.vehicle_photo_file_id = "vehicle_xyz"
        await db_session.commit()
        await db_session.refresh(user)

        assert user.selfie_file_id == "selfie_abc"
        assert user.vehicle_category == "car_4"
        assert user.vehicle_photo_file_id == "vehicle_xyz"


# ============================================================================
# תהליך אישור/דחיית שליחים
# ============================================================================


class TestCourierApprovalService:
    """בדיקות ל-CourierApprovalService - לוגיקת אישור/דחייה משותפת"""

    @pytest.mark.asyncio
    async def test_approve_pending_courier(self, db_session, user_factory):
        """אישור שליח ממתין"""
        user = await user_factory(
            phone_number="tg:60001",
            name="Pending Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="60001",
            approval_status=ApprovalStatus.PENDING,
        )
        result = await CourierApprovalService.approve(db_session, user.id)
        assert result.success is True
        assert "אושר" in result.message
        assert result.user.approval_status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_approve_already_approved(self, db_session, user_factory):
        """אישור שליח שכבר מאושר"""
        user = await user_factory(
            phone_number="tg:60002",
            name="Approved Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="60002",
            approval_status=ApprovalStatus.APPROVED,
        )
        result = await CourierApprovalService.approve(db_session, user.id)
        assert result.success is False
        assert "כבר מאושר" in result.message

    @pytest.mark.asyncio
    async def test_approve_blocked_courier(self, db_session, user_factory):
        """אישור שליח חסום - נכשל"""
        user = await user_factory(
            phone_number="tg:60003",
            name="Blocked Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="60003",
            approval_status=ApprovalStatus.BLOCKED,
        )
        result = await CourierApprovalService.approve(db_session, user.id)
        assert result.success is False
        assert "חסום" in result.message

    @pytest.mark.asyncio
    async def test_approve_nonexistent_user(self, db_session):
        """אישור משתמש שלא קיים"""
        result = await CourierApprovalService.approve(db_session, 99999)
        assert result.success is False
        assert "לא נמצא" in result.message

    @pytest.mark.asyncio
    async def test_approve_non_courier(self, db_session, user_factory):
        """אישור משתמש שאינו שליח"""
        user = await user_factory(
            phone_number="tg:60004",
            name="Sender User",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="60004",
        )
        result = await CourierApprovalService.approve(db_session, user.id)
        assert result.success is False
        assert "אינו נהג" in result.message

    @pytest.mark.asyncio
    async def test_reject_pending_courier(self, db_session, user_factory):
        """דחיית שליח ממתין"""
        user = await user_factory(
            phone_number="tg:60005",
            name="To Reject",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="60005",
            approval_status=ApprovalStatus.PENDING,
        )
        result = await CourierApprovalService.reject(db_session, user.id)
        assert result.success is True
        assert "נדחה" in result.message
        assert result.user.approval_status == ApprovalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_reject_already_rejected(self, db_session, user_factory):
        """דחיית שליח שכבר נדחה"""
        user = await user_factory(
            phone_number="tg:60006",
            name="Already Rejected",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="60006",
            approval_status=ApprovalStatus.REJECTED,
        )
        result = await CourierApprovalService.reject(db_session, user.id)
        assert result.success is False
        assert "כבר נדחה" in result.message

    @pytest.mark.asyncio
    async def test_approve_courier_who_reverted_to_sender_via_hash(self, db_session, user_factory):
        """אישור שליח שלחץ # וחזר להיות SENDER - עדיין צריך לעבוד"""
        user = await user_factory(
            phone_number="tg:60007",
            name="Reverted Courier",
            role=UserRole.SENDER,  # חזר ל-SENDER אחרי לחיצה על #
            platform="telegram",
            telegram_chat_id="60007",
            approval_status=ApprovalStatus.PENDING,  # עדיין PENDING מהרישום
        )
        result = await CourierApprovalService.approve(db_session, user.id)
        assert result.success is True
        assert "אושר" in result.message
        assert result.user.approval_status == ApprovalStatus.APPROVED
        assert result.user.role == UserRole.COURIER  # חזר ל-COURIER

    @pytest.mark.asyncio
    async def test_reject_courier_who_reverted_to_sender_via_hash(self, db_session, user_factory):
        """דחיית שליח שלחץ # וחזר להיות SENDER - עדיין צריך לעבוד"""
        user = await user_factory(
            phone_number="tg:60008",
            name="Reverted Courier 2",
            role=UserRole.SENDER,  # חזר ל-SENDER אחרי לחיצה על #
            platform="telegram",
            telegram_chat_id="60008",
            approval_status=ApprovalStatus.PENDING,
        )
        result = await CourierApprovalService.reject(db_session, user.id)
        assert result.success is True
        assert "נדחה" in result.message
        assert result.user.approval_status == ApprovalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_approve_sender_without_pending_still_fails(self, db_session, user_factory):
        """אישור SENDER ללא סטטוס PENDING - עדיין צריך להיכשל"""
        user = await user_factory(
            phone_number="tg:60009",
            name="Regular Sender",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="60009",
            # approval_status is None - שולח רגיל שלא עבר רישום
        )
        result = await CourierApprovalService.approve(db_session, user.id)
        assert result.success is False
        assert "אינו נהג" in result.message


class TestTelegramApprovalButtons:
    """בדיקות לכפתורי אישור/דחייה בטלגרם"""

    @pytest.mark.asyncio
    async def test_approve_button_callback(
        self, test_client: AsyncClient, user_factory, mock_telegram_api
    ):
        """כפתור אישור inline בטלגרם מאשר את השליח"""
        courier = await user_factory(
            phone_number="tg:70001",
            name="To Approve TG",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="70001",
            approval_status=ApprovalStatus.PENDING,
        )
        admin_chat_id = "99999"
        with (
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_IDS", admin_chat_id),
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_ID", admin_chat_id),
            patch.object(settings, "TELEGRAM_BOT_TOKEN", "fake-token"),
            patch.object(settings, "WHATSAPP_ADMIN_GROUP_ID", None),
            patch.object(settings, "WHATSAPP_ADMIN_NUMBERS", ""),
            patch.object(settings, "WHATSAPP_GATEWAY_URL", ""),
        ):
            resp = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 1,
                    "callback_query": {
                        "id": "cb1",
                        "from": {"id": 99999, "first_name": "Admin"},
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 99999, "type": "private"},
                            "from": {"id": 99999, "first_name": "Admin"},
                            "date": 1700000000,
                            "text": "כרטיס נהג",
                        },
                        "data": f"approve_courier_{courier.id}",
                    },
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("admin_action") == "approve"

    @pytest.mark.asyncio
    async def test_reject_button_callback(
        self, test_client: AsyncClient, user_factory, mock_telegram_api
    ):
        """כפתור דחייה inline בטלגרם דוחה את השליח"""
        courier = await user_factory(
            phone_number="tg:70002",
            name="To Reject TG",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="70002",
            approval_status=ApprovalStatus.PENDING,
        )
        admin_chat_id = "99998"
        with (
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_IDS", admin_chat_id),
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_ID", admin_chat_id),
            patch.object(settings, "TELEGRAM_BOT_TOKEN", "fake-token"),
            patch.object(settings, "WHATSAPP_ADMIN_GROUP_ID", None),
            patch.object(settings, "WHATSAPP_ADMIN_NUMBERS", ""),
            patch.object(settings, "WHATSAPP_GATEWAY_URL", ""),
        ):
            resp = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 2,
                    "callback_query": {
                        "id": "cb2",
                        "from": {"id": 99998, "first_name": "Admin2"},
                        "message": {
                            "message_id": 2,
                            "chat": {"id": 99998, "type": "private"},
                            "from": {"id": 99998, "first_name": "Admin2"},
                            "date": 1700000000,
                            "text": "כרטיס נהג",
                        },
                        "data": f"reject_courier_{courier.id}",
                    },
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("admin_action") == "reject_pending_note"


class TestWhatsAppAdminApproval:
    """בדיקות לפקודות אישור/דחייה מפרטי של מנהלים בוואטסאפ"""

    @pytest.mark.asyncio
    async def test_whatsapp_command_matching(self):
        """זיהוי פקודות אישור/דחייה"""
        from app.api.webhooks.whatsapp import _match_approval_command

        # אישור
        assert _match_approval_command("אשר 123") == ("approve", 123, "courier", None)
        assert _match_approval_command("✅ אשר 456") == ("approve", 456, "courier", None)
        assert _match_approval_command("אשר שליח 789") == ("approve", 789, "courier", None)

        # דחייה
        assert _match_approval_command("דחה 123") == ("reject", 123, "courier", None)
        assert _match_approval_command("❌ דחה 456") == ("reject", 456, "courier", None)
        assert _match_approval_command("דחה שליח 789") == ("reject", 789, "courier", None)

        # דחייה עם הערה
        assert _match_approval_command("דחה 123 התמונות לא ברורות") == ("reject", 123, "courier", "התמונות לא ברורות")
        assert _match_approval_command("❌ דחה שליח 456 חסר מסמך") == ("reject", 456, "courier", "חסר מסמך")
        assert _match_approval_command("דחייה 789 צריך לשלוח תמונה חדשה") == ("reject", 789, "courier", "צריך לשלוח תמונה חדשה")

        # לא תקין
        assert _match_approval_command("שלום") is None
        assert _match_approval_command("הודעה אחרת") is None


class TestRejectionNote:
    """בדיקות להערת דחייה - שמירה, הצגה והעברה לנהג"""

    @pytest.mark.asyncio
    async def test_reject_with_note_saves_to_db(self, db_session, user_factory):
        """דחייה עם הערה שומרת את ההערה ב-DB"""
        user = await user_factory(
            phone_number="tg:80001",
            name="Note Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80001",
            approval_status=ApprovalStatus.PENDING,
        )
        result = await CourierApprovalService.reject(
            db_session, user.id, rejection_note="התמונות לא ברורות"
        )
        assert result.success is True
        assert result.user.rejection_note == "התמונות לא ברורות"
        assert "הערה" in result.message
        assert "התמונות לא ברורות" in result.message

    @pytest.mark.asyncio
    async def test_reject_without_note_no_note_in_db(self, db_session, user_factory):
        """דחייה ללא הערה - אין הערה ב-DB (תאימות לאחור)"""
        user = await user_factory(
            phone_number="tg:80002",
            name="No Note Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80002",
            approval_status=ApprovalStatus.PENDING,
        )
        result = await CourierApprovalService.reject(db_session, user.id)
        assert result.success is True
        assert result.user.rejection_note is None
        assert "הערה" not in result.message

    @pytest.mark.asyncio
    async def test_rejected_courier_sees_note_in_pending_approval(self, db_session, user_factory):
        """נהג שנדחה רואה את הערת הדחייה בהודעת pending_approval"""
        user = await user_factory(
            phone_number="tg:80003",
            name="Rejected Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80003",
            approval_status=ApprovalStatus.PENDING,
        )
        # דחייה עם הערה
        await CourierApprovalService.reject(
            db_session, user.id, rejection_note="צילום הרכב לא ברור"
        )

        # סימולציה של הודעה מהנהג - אמור לראות את ההערה
        handler = CourierStateHandler(db_session)
        response, new_state, _ = await handler._handle_pending_approval(user, "שלום", {}, None)
        assert "צילום הרכב לא ברור" in response.text
        assert "הערת המנהל" in response.text

    @pytest.mark.asyncio
    async def test_rejected_courier_without_note_no_note_line(self, db_session, user_factory):
        """נהג שנדחה ללא הערה - לא מוצגת שורת הערה"""
        user = await user_factory(
            phone_number="tg:80004",
            name="Rejected No Note",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80004",
            approval_status=ApprovalStatus.PENDING,
        )
        await CourierApprovalService.reject(db_session, user.id)

        handler = CourierStateHandler(db_session)
        response, new_state, _ = await handler._handle_pending_approval(user, "שלום", {}, None)
        assert "הערת המנהל" not in response.text
        assert "נדחתה" in response.text

    @pytest.mark.asyncio
    async def test_telegram_reject_button_asks_for_note(
        self, test_client: AsyncClient, user_factory, mock_telegram_api, fake_redis
    ):
        """כפתור דחייה בטלגרם מבקש הערה במקום לדחות מיד"""
        from app.api.webhooks.telegram import _PENDING_REJECTION_KEY_PREFIX

        courier = await user_factory(
            phone_number="tg:80005",
            name="TG Reject Note",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80005",
            approval_status=ApprovalStatus.PENDING,
        )
        admin_chat_id = "99990"
        with (
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_IDS", admin_chat_id),
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_ID", admin_chat_id),
            patch.object(settings, "TELEGRAM_BOT_TOKEN", "fake-token"),
            patch.object(settings, "WHATSAPP_ADMIN_GROUP_ID", None),
            patch.object(settings, "WHATSAPP_ADMIN_NUMBERS", ""),
            patch.object(settings, "WHATSAPP_GATEWAY_URL", ""),
        ):
            resp = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 100,
                    "callback_query": {
                        "id": "cb_note1",
                        "from": {"id": 99990, "first_name": "Admin"},
                        "message": {
                            "message_id": 100,
                            "chat": {"id": 99990, "type": "private"},
                            "from": {"id": 99990, "first_name": "Admin"},
                            "date": 1700000000,
                            "text": "כרטיס נהג",
                        },
                        "data": f"reject_courier_{courier.id}",
                    },
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("admin_action") == "reject_pending_note"
            # הנהג עדיין לא נדחה - ממתינים להערה ב-Redis
            key = f"{_PENDING_REJECTION_KEY_PREFIX}{admin_chat_id}"
            stored = await fake_redis.get(key)
            assert stored == f"courier:{courier.id}"

    @pytest.mark.asyncio
    async def test_whatsapp_reject_with_note(self):
        """פקודת דחייה עם הערה בוואטסאפ"""
        from app.api.webhooks.whatsapp import _match_approval_command

        result = _match_approval_command("דחה 123 התמונות לא ברורות")
        assert result is not None
        action, user_id, target_type, note = result
        assert action == "reject"
        assert user_id == 123
        assert note == "התמונות לא ברורות"

    @pytest.mark.asyncio
    async def test_whatsapp_reject_without_note_backwards_compatible(self):
        """פקודת דחייה ללא הערה - תאימות לאחור"""
        from app.api.webhooks.whatsapp import _match_approval_command

        result = _match_approval_command("דחה 123")
        assert result is not None
        action, user_id, target_type, note = result
        assert action == "reject"
        assert user_id == 123
        assert note is None

    @pytest.mark.asyncio
    async def test_reject_already_approved_courier_fails(self, db_session, user_factory):
        """דחיית נהג שכבר אושר — נכשלת (race condition בין מנהלים)"""
        user = await user_factory(
            phone_number="tg:80010",
            name="Already Approved",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80010",
            approval_status=ApprovalStatus.APPROVED,
        )
        result = await CourierApprovalService.reject(
            db_session, user.id, rejection_note="טעות"
        )
        assert result.success is False
        assert "כבר מאושר" in result.message

    @pytest.mark.asyncio
    async def test_redis_get_returns_none_when_missing(self, fake_redis):
        """כשאין רשומה ב-Redis — _get_pending_rejection מחזיר None"""
        from app.api.webhooks.telegram import _get_pending_rejection

        result = await _get_pending_rejection("nonexistent_admin")
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_set_and_get_pending_rejection(self, fake_redis):
        """שמירה ושליפה מ-Redis — _set ו-_get עובדים נכון"""
        from app.api.webhooks.telegram import (
            _set_pending_rejection,
            _get_pending_rejection,
        )

        await _set_pending_rejection("admin_123", 555, target_type="courier")
        result = await _get_pending_rejection("admin_123")
        assert result == ("courier", 555)

    @pytest.mark.asyncio
    async def test_redis_pop_returns_and_deletes(self, fake_redis):
        """_pop_pending_rejection מחזיר ומוחק אטומית"""
        from app.api.webhooks.telegram import (
            _set_pending_rejection,
            _pop_pending_rejection,
            _get_pending_rejection,
        )

        await _set_pending_rejection("admin_pop", 777, target_type="courier")
        result = await _pop_pending_rejection("admin_pop")
        assert result == ("courier", 777)
        # הרשומה נמחקה
        assert await _get_pending_rejection("admin_pop") is None

    @pytest.mark.asyncio
    async def test_redis_clear_deletes_pending(self, fake_redis):
        """_clear_pending_rejection מוחק רשומה קיימת"""
        from app.api.webhooks.telegram import (
            _set_pending_rejection,
            _get_pending_rejection,
            _clear_pending_rejection,
        )

        await _set_pending_rejection("admin_clear", 888)
        await _clear_pending_rejection("admin_clear")
        assert await _get_pending_rejection("admin_clear") is None

    @pytest.mark.asyncio
    async def test_html_escaping_in_rejection_note(self, db_session, user_factory):
        """הערת דחייה עם תווים מיוחדים — נעשה HTML escape בתגובה לשליח (טלגרם)"""
        from app.state_machine.handlers import CourierStateHandler

        xss_note = '<script>alert("xss")</script> & "quotes"'
        user = await user_factory(
            phone_number="tg:80020",
            name="XSS Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80020",
            approval_status=ApprovalStatus.PENDING,
        )
        # דחייה עם הערה שמכילה תווי HTML
        result = await CourierApprovalService.reject(
            db_session, user.id, rejection_note=xss_note
        )
        assert result.success is True

        await db_session.refresh(user)
        assert user.rejection_note == xss_note

        # בדיקת ה-handler (טלגרם): ההודעה לשליח חייבת להכיל את ההערה ב-escape
        handler = CourierStateHandler(db_session, platform="telegram")
        blocked_resp = handler._blocked_or_rejected_response(user)
        assert blocked_resp is not None
        response, _, _ = blocked_resp
        # לוודא שהתווים עברו escape ולא מופיעים כ-raw HTML
        assert "<script>" not in response.text
        assert "&lt;script&gt;" in response.text
        assert "&amp;" in response.text
        assert "&quot;" in response.text

        # בדיקת ה-handler (וואטסאפ): ההערה מוצגת כטקסט רגיל ללא escape
        wa_handler = CourierStateHandler(db_session, platform="whatsapp")
        wa_resp = wa_handler._blocked_or_rejected_response(user)
        assert wa_resp is not None
        wa_response, _, _ = wa_resp
        assert xss_note in wa_response.text

    @pytest.mark.asyncio
    async def test_html_escaping_in_notify_after_decision(self):
        """sanitize_for_html מבצע escape נכון לתווי HTML"""
        from app.core.validation import TextSanitizer

        dangerous = '<b>bold</b> & "air quotes"'
        safe = TextSanitizer.sanitize_for_html(dangerous)
        assert "<b>" not in safe
        assert "&lt;b&gt;" in safe
        assert "&amp;" in safe
        assert "&quot;" in safe

    @pytest.mark.asyncio
    async def test_double_reject_click_overwrites_pending(
        self, db_session, user_factory, test_client, fake_redis
    ):
        """מנהל שלוחץ דחה פעמיים — הלחיצה השנייה דורסת את הראשונה"""
        from app.api.webhooks.telegram import _PENDING_REJECTION_KEY_PREFIX

        courier1 = await user_factory(
            phone_number="tg:80030",
            name="Courier One",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80030",
            approval_status=ApprovalStatus.PENDING,
        )
        courier2 = await user_factory(
            phone_number="tg:80031",
            name="Courier Two",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80031",
            approval_status=ApprovalStatus.PENDING,
        )
        admin_chat_id = "99991"
        key = f"{_PENDING_REJECTION_KEY_PREFIX}{admin_chat_id}"
        with (
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_IDS", admin_chat_id),
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_ID", admin_chat_id),
            patch.object(settings, "TELEGRAM_BOT_TOKEN", "fake-token"),
            patch.object(settings, "WHATSAPP_ADMIN_GROUP_ID", None),
            patch.object(settings, "WHATSAPP_ADMIN_NUMBERS", ""),
            patch.object(settings, "WHATSAPP_GATEWAY_URL", ""),
        ):
            # לחיצה ראשונה — דחיית שליח 1
            resp1 = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 200,
                    "callback_query": {
                        "id": "cb_double1",
                        "from": {"id": 99991, "first_name": "Admin"},
                        "message": {
                            "message_id": 200,
                            "chat": {"id": 99991, "type": "private"},
                            "from": {"id": 99991, "first_name": "Admin"},
                            "date": 1700000000,
                            "text": "כרטיס נהג",
                        },
                        "data": f"reject_courier_{courier1.id}",
                    },
                },
            )
            assert resp1.status_code == 200
            assert await fake_redis.get(key) == f"courier:{courier1.id}"

            # לחיצה שנייה — דחיית שליח 2, דורסת את הראשונה
            resp2 = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 201,
                    "callback_query": {
                        "id": "cb_double2",
                        "from": {"id": 99991, "first_name": "Admin"},
                        "message": {
                            "message_id": 201,
                            "chat": {"id": 99991, "type": "private"},
                            "from": {"id": 99991, "first_name": "Admin"},
                            "date": 1700000000,
                            "text": "כרטיס נהג",
                        },
                        "data": f"reject_courier_{courier2.id}",
                    },
                },
            )
            assert resp2.status_code == 200
            # הרשומה הנוכחית היא של שליח 2
            assert await fake_redis.get(key) == f"courier:{courier2.id}"

    @pytest.mark.asyncio
    async def test_approve_clears_pending_rejection(
        self, db_session, user_factory, test_client, fake_redis
    ):
        """לחיצת אשר אחרי דחה — מנקה את הדחייה הממתינה"""
        from app.api.webhooks.telegram import _PENDING_REJECTION_KEY_PREFIX

        courier1 = await user_factory(
            phone_number="tg:80040",
            name="Courier Pending Reject",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80040",
            approval_status=ApprovalStatus.PENDING,
        )
        courier2 = await user_factory(
            phone_number="tg:80041",
            name="Courier To Approve",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80041",
            approval_status=ApprovalStatus.PENDING,
        )
        admin_chat_id = "99992"
        key = f"{_PENDING_REJECTION_KEY_PREFIX}{admin_chat_id}"
        with (
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_IDS", admin_chat_id),
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_ID", admin_chat_id),
            patch.object(settings, "TELEGRAM_BOT_TOKEN", "fake-token"),
            patch.object(settings, "WHATSAPP_ADMIN_GROUP_ID", None),
            patch.object(settings, "WHATSAPP_ADMIN_NUMBERS", ""),
            patch.object(settings, "WHATSAPP_GATEWAY_URL", ""),
        ):
            # שלב 1: לחיצה על דחה לשליח 1
            resp1 = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 300,
                    "callback_query": {
                        "id": "cb_reject_then_approve_1",
                        "from": {"id": 99992, "first_name": "Admin"},
                        "message": {
                            "message_id": 300,
                            "chat": {"id": 99992, "type": "private"},
                            "from": {"id": 99992, "first_name": "Admin"},
                            "date": 1700000000,
                            "text": "כרטיס נהג",
                        },
                        "data": f"reject_courier_{courier1.id}",
                    },
                },
            )
            assert resp1.status_code == 200
            assert await fake_redis.get(key) is not None

            # שלב 2: לחיצה על אשר לשליח 2 — חייבת לנקות את הדחייה הממתינה
            resp2 = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 301,
                    "callback_query": {
                        "id": "cb_reject_then_approve_2",
                        "from": {"id": 99992, "first_name": "Admin"},
                        "message": {
                            "message_id": 301,
                            "chat": {"id": 99992, "type": "private"},
                            "from": {"id": 99992, "first_name": "Admin"},
                            "date": 1700000000,
                            "text": "כרטיס נהג",
                        },
                        "data": f"approve_courier_{courier2.id}",
                    },
                },
            )
            assert resp2.status_code == 200
            # הדחייה הממתינה נמחקה — הטקסט הבא לא ידחה בטעות את שליח 1
            assert await fake_redis.get(key) is None

    @pytest.mark.asyncio
    async def test_pending_rejection_uses_configured_ttl(
        self, test_client: AsyncClient, user_factory, mock_telegram_api, fake_redis
    ):
        """דחייה ממתינה נשמרת עם TTL מהגדרות (REDIS_PENDING_REJECTION_TTL)"""
        from app.api.webhooks.telegram import _PENDING_REJECTION_KEY_PREFIX

        courier = await user_factory(
            phone_number="tg:80050",
            name="TTL Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80050",
            approval_status=ApprovalStatus.PENDING,
        )
        admin_chat_id = "99993"
        key = f"{_PENDING_REJECTION_KEY_PREFIX}{admin_chat_id}"
        with (
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_IDS", admin_chat_id),
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_ID", admin_chat_id),
            patch.object(settings, "TELEGRAM_BOT_TOKEN", "fake-token"),
            patch.object(settings, "WHATSAPP_ADMIN_GROUP_ID", None),
            patch.object(settings, "WHATSAPP_ADMIN_NUMBERS", ""),
            patch.object(settings, "WHATSAPP_GATEWAY_URL", ""),
        ):
            resp = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 400,
                    "callback_query": {
                        "id": "cb_ttl",
                        "from": {"id": 99993, "first_name": "Admin"},
                        "message": {
                            "message_id": 400,
                            "chat": {"id": 99993, "type": "private"},
                            "from": {"id": 99993, "first_name": "Admin"},
                            "date": 1700000000,
                            "text": "כרטיס נהג",
                        },
                        "data": f"reject_courier_{courier.id}",
                    },
                },
            )
            assert resp.status_code == 200
            # TTL שנשמר ב-FakeRedis חייב להתאים להגדרה
            assert fake_redis._ttls.get(key) == settings.REDIS_PENDING_REJECTION_TTL

    @pytest.mark.asyncio
    async def test_redis_failure_rejects_without_note(
        self, db_session, user_factory, test_client, mock_telegram_api
    ):
        """כשל Redis — הדחייה מבוצעת ישירות ללא הערה והמנהל מקבל הודעה"""
        courier = await user_factory(
            phone_number="tg:80060",
            name="Redis Fail Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="80060",
            approval_status=ApprovalStatus.PENDING,
        )
        admin_chat_id = "99994"

        # FakeRedis שנכשל בכל פעולה
        class FailingRedis:
            async def ping(self) -> bool:
                return True

            async def setex(self, *args, **kwargs):
                raise ConnectionError("Redis is down")

            async def get(self, *args, **kwargs):
                raise ConnectionError("Redis is down")

            async def getdel(self, *args, **kwargs):
                raise ConnectionError("Redis is down")

            async def delete(self, *args, **kwargs):
                raise ConnectionError("Redis is down")

        async def _get_failing_redis():
            return FailingRedis()

        with (
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_IDS", admin_chat_id),
            patch.object(settings, "TELEGRAM_ADMIN_CHAT_ID", admin_chat_id),
            patch.object(settings, "TELEGRAM_BOT_TOKEN", "fake-token"),
            patch.object(settings, "WHATSAPP_ADMIN_GROUP_ID", None),
            patch.object(settings, "WHATSAPP_ADMIN_NUMBERS", ""),
            patch.object(settings, "WHATSAPP_GATEWAY_URL", ""),
            patch("app.core.redis_client.get_redis", _get_failing_redis),
        ):
            resp = await test_client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 500,
                    "callback_query": {
                        "id": "cb_redis_fail",
                        "from": {"id": 99994, "first_name": "Admin"},
                        "message": {
                            "message_id": 500,
                            "chat": {"id": 99994, "type": "private"},
                            "from": {"id": 99994, "first_name": "Admin"},
                            "date": 1700000000,
                            "text": "כרטיס נהג",
                        },
                        "data": f"reject_courier_{courier.id}",
                    },
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            # כשל Redis — דחייה ישירה ללא הערה
            assert data.get("admin_action") == "reject_immediate_redis_fail"

            # הנהג צריך להיות נדחה ב-DB
            await db_session.refresh(courier)
            assert courier.approval_status == ApprovalStatus.REJECTED


# ============================================================================
# בדיקות ידניות שנגזרו מ-property-based testing (hypothesis)
# ============================================================================


class TestPropertyBasedDiscoveries:
    """
    בדיקות ידניות שנוצרו כתוצאה מהרצת hypothesis
    על זרימות אישור/דחייה ופירוס פקודות.
    ראו issue #138 ו-tests/test_property_based.py.
    """

    @pytest.mark.asyncio
    async def test_rapid_approve_reject_approve_sequence(self, db_session, user_factory):
        """
        רצף מהיר: approve → reject → approve.
        נמצא ע"י hypothesis — מוודא שהסטטוס הסופי תקין
        ושהשירות לא נותן תוצאות שגויות אחרי מעברים מרובים.
        """
        user = await user_factory(
            phone_number="tg:disc_001",
            name="Rapid Seq",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="disc_001",
            approval_status=ApprovalStatus.PENDING,
        )
        # אישור ראשון — אמור להצליח
        r1 = await CourierApprovalService.approve(db_session, user.id)
        assert r1.success is True

        # דחייה אחרי אישור — אמורה להיכשל (לא ניתן לדחות מאושר)
        r2 = await CourierApprovalService.reject(db_session, user.id)
        assert r2.success is False
        assert "כבר מאושר" in r2.message

        # אישור כפול — אמור להיכשל
        r3 = await CourierApprovalService.approve(db_session, user.id)
        assert r3.success is False
        assert "כבר מאושר" in r3.message

        # הסטטוס הסופי חייב להישאר APPROVED
        await db_session.refresh(user)
        assert user.approval_status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_reject_with_note_then_reject_again_fails(self, db_session, user_factory):
        """
        דחייה עם הערה ואז דחייה נוספת — הדחייה השנייה נכשלת.
        מוודא שאי אפשר לדחות פעמיים.
        """
        user = await user_factory(
            phone_number="tg:disc_002",
            name="Double Reject",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="disc_002",
            approval_status=ApprovalStatus.PENDING,
        )
        r1 = await CourierApprovalService.reject(
            db_session, user.id, rejection_note="צילום לא ברור"
        )
        assert r1.success is True
        assert user.rejection_note == "צילום לא ברור"

        r2 = await CourierApprovalService.reject(
            db_session, user.id, rejection_note="הערה אחרת"
        )
        assert r2.success is False
        assert "כבר נדחה" in r2.message

        # ההערה הראשונה נשארת — לא נדרסת
        await db_session.refresh(user)
        assert user.rejection_note == "צילום לא ברור"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("empty_note", [None, "", "   "])
    async def test_empty_rejection_note_stored_as_none(self, empty_note, db_session, user_factory):
        """
        הערת דחייה ריקה (None / "" / רווחים) — נשמרת כ-None, לא כמחרוזת ריקה.
        באג שנמצא ע"י hypothesis: העברת "" ל-reject() גרמה לשמירת מחרוזת ריקה ב-DB.
        """
        uid = f"tg:disc_003_{hash(str(empty_note))}"
        user = await user_factory(
            phone_number=uid,
            name="Empty Note",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id=uid,
            approval_status=ApprovalStatus.PENDING,
        )
        result = await CourierApprovalService.reject(
            db_session, user.id, rejection_note=empty_note
        )
        assert result.success is True
        await db_session.refresh(user)
        assert user.rejection_note is None, (
            f"rejection_note עם קלט {empty_note!r} אמור להיות None, קיבלנו: {user.rejection_note!r}"
        )

    @pytest.mark.unit
    def test_approval_command_with_unicode_surrogates_does_not_crash(self):
        """
        פקודות עם תווי Unicode חריגים — הפונקציה לא קורסת.
        נמצא ע"י hypothesis (arbitrary text strategy).
        """
        from app.api.webhooks.whatsapp import _match_approval_command

        # תווים חריגים שנמצאו בהרצות hypothesis
        edge_cases = [
            "\x00אשר 123",
            "אשר\x00 456",
            "\u200b\u200c\u200d אשר 789",
            "✅\ufeff אשר 42",
            "\u202b\u202a\u202c אשר 55 \u202e",
        ]
        for text in edge_cases:
            result = _match_approval_command(text)
            # לא אמור לקרוס — None או tuple חוקי
            if result is not None:
                action, uid, _, note = result
                assert action in ("approve", "reject")
                assert isinstance(uid, int) and uid > 0

    @pytest.mark.unit
    def test_approval_command_whitespace_only_note_normalized_to_none(self):
        """
        פקודת דחייה עם הערה שמכילה רק רווחים — מנורמלת ל-None.
        נמצא ע"י hypothesis (rejection note strategy).
        """
        from app.api.webhooks.whatsapp import _match_approval_command

        result = _match_approval_command("דחה 100   ")
        assert result is not None
        _, _, _, note = result
        assert note is None, f"הערה של רווחים בלבד הייתה אמורה להיות None, קיבלנו: '{note}'"

    @pytest.mark.asyncio
    async def test_all_actions_on_blocked_courier_fail(self, db_session, user_factory):
        """
        שליח חסום — כל הפעולות (אישור, דחייה) נכשלות.
        נמצא ע"י hypothesis — רצף שמתחיל ב-block ואז פעולות נוספות.
        """
        user = await user_factory(
            phone_number="tg:disc_005",
            name="Blocked Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="disc_005",
            approval_status=ApprovalStatus.BLOCKED,
        )
        r_approve = await CourierApprovalService.approve(db_session, user.id)
        assert r_approve.success is False
        assert "חסום" in r_approve.message

        r_reject = await CourierApprovalService.reject(db_session, user.id)
        assert r_reject.success is False
        assert "חסום" in r_reject.message

        # הסטטוס נשאר BLOCKED
        await db_session.refresh(user)
        assert user.approval_status == ApprovalStatus.BLOCKED
