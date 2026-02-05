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
            "/api/webhooks/whatsapp/webhook",
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

        async def post(text: str, media_url: str = None, media_type: str = None) -> dict:
            msg = {
                "from_number": reply_to,
                "sender_id": sender_id,
                "reply_to": reply_to,
                "message_id": "m1",
                "text": text,
                "timestamp": 1700000000,
            }
            if media_url:
                msg["media_url"] = media_url
                msg["media_type"] = media_type or "image"
            r = await test_client.post(
                "/api/webhooks/whatsapp/webhook",
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
        assert "אינו שליח" in result.message

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
            assert data.get("admin_action") == "reject"


class TestWhatsAppAdminApproval:
    """בדיקות לפקודות אישור/דחייה מפרטי של מנהלים בוואטסאפ"""

    @pytest.mark.asyncio
    async def test_whatsapp_command_matching(self):
        """זיהוי פקודות אישור/דחייה"""
        from app.api.webhooks.whatsapp import _match_approval_command

        # אישור
        assert _match_approval_command("אשר 123") == ("approve", 123)
        assert _match_approval_command("✅ אשר 456") == ("approve", 456)
        assert _match_approval_command("אשר שליח 789") == ("approve", 789)

        # דחייה
        assert _match_approval_command("דחה 123") == ("reject", 123)
        assert _match_approval_command("❌ דחה 456") == ("reject", 456)
        assert _match_approval_command("דחה שליח 789") == ("reject", 789)

        # לא תקין
        assert _match_approval_command("שלום") is None
        assert _match_approval_command("הודעה אחרת") is None
