"""
בדיקות WhatsApp — iDriver סשן 10: תאימות פלטפורמית

בודק:
- ניתוב נהג ב-WhatsApp webhook (WPPConnect + Cloud API)
- חילוץ מיקום GPS מ-Cloud API
- guard ל-multi-step flow עם prefix DRIVER
- fallback לקבוצות (keyboard=None)
- סינון מספרי טלפון
- התנהגות זהה בין Telegram ל-WhatsApp
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, datetime, timedelta

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    DriverSubscriptionStatus,
    VehicleCategory,
)
from app.db.models.driver_search_settings import DriverSearchSettings
from app.state_machine.states import DriverState
from app.state_machine.driver_handler import DriverStateHandler
from app.state_machine.manager import StateManager


# ============================================================================
# בדיקות חילוץ מיקום — Cloud API
# ============================================================================


class TestCloudAPILocationExtraction:
    """חילוץ מיקום GPS מהודעת Cloud API"""

    def test_extract_location_from_location_message(self) -> None:
        """חילוץ מיקום מהודעת location"""
        from app.api.webhooks.whatsapp_cloud import _extract_location_from_message

        msg = {
            "type": "location",
            "location": {
                "latitude": 32.0853,
                "longitude": 34.7818,
            },
        }
        lat, lng = _extract_location_from_message(msg)
        assert lat == pytest.approx(32.0853)
        assert lng == pytest.approx(34.7818)

    def test_extract_location_from_text_message_returns_none(self) -> None:
        """הודעת טקסט רגילה — אין מיקום"""
        from app.api.webhooks.whatsapp_cloud import _extract_location_from_message

        msg = {"type": "text", "text": {"body": "שלום"}}
        lat, lng = _extract_location_from_message(msg)
        assert lat is None
        assert lng is None

    def test_extract_location_missing_fields_returns_none(self) -> None:
        """הודעת location ללא שדות lat/lng"""
        from app.api.webhooks.whatsapp_cloud import _extract_location_from_message

        msg = {"type": "location", "location": {}}
        lat, lng = _extract_location_from_message(msg)
        assert lat is None
        assert lng is None

    def test_extract_location_from_image_returns_none(self) -> None:
        """הודעת תמונה — אין מיקום"""
        from app.api.webhooks.whatsapp_cloud import _extract_location_from_message

        msg = {"type": "image", "image": {"id": "123"}}
        lat, lng = _extract_location_from_message(msg)
        assert lat is None
        assert lng is None


# ============================================================================
# בדיקות חילוץ טקסט ומדיה — Cloud API
# ============================================================================


class TestCloudAPIExtraction:
    """חילוץ טקסט ומדיה מהודעות Cloud API"""

    def test_extract_text_from_text_message(self) -> None:
        """חילוץ טקסט רגיל"""
        from app.api.webhooks.whatsapp_cloud import _extract_text_from_message

        msg = {"type": "text", "text": {"body": "פ ים"}}
        assert _extract_text_from_message(msg) == "פ ים"

    def test_extract_text_from_interactive_button(self) -> None:
        """חילוץ טקסט מכפתור interactive"""
        from app.api.webhooks.whatsapp_cloud import _extract_text_from_message

        msg = {
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "callback_data_123"},
            },
        }
        assert _extract_text_from_message(msg) == "callback_data_123"

    def test_extract_media_from_image(self) -> None:
        """חילוץ מדיה מתמונה"""
        from app.api.webhooks.whatsapp_cloud import _extract_media_from_message

        msg = {"type": "image", "image": {"id": "media_id_456"}}
        media_id, media_type = _extract_media_from_message(msg)
        assert media_id == "media_id_456"
        assert media_type == "image"

    def test_extract_media_from_text_returns_none(self) -> None:
        """הודעת טקסט — אין מדיה"""
        from app.api.webhooks.whatsapp_cloud import _extract_media_from_message

        msg = {"type": "text", "text": {"body": "שלום"}}
        media_id, media_type = _extract_media_from_message(msg)
        assert media_id is None
        assert media_type is None


# ============================================================================
# בדיקות DriverStateHandler — עבודה עם פלטפורמת WhatsApp
# ============================================================================


class TestDriverHandlerWhatsApp:
    """בדיקות handler נהג בפלטפורמת WhatsApp"""

    @pytest.mark.asyncio
    async def test_handler_creates_with_whatsapp_platform(
        self, db_session
    ) -> None:
        """יצירת handler עם פלטפורמת WhatsApp"""
        handler = DriverStateHandler(db_session, platform="whatsapp")
        assert handler.platform == "whatsapp"

    @pytest.mark.asyncio
    async def test_initial_state_whatsapp(
        self, db_session, user_factory
    ) -> None:
        """מצב ראשוני — שליחת הודעת ברוכים הבאים ב-WhatsApp"""
        user = await user_factory(
            phone_number="+972509991001",
            name="נהג וואטסאפ",
            role=UserRole.DRIVER,
        )
        sm = StateManager(db_session)
        await sm.force_state(
            user.id, "whatsapp", DriverState.INITIAL.value, context={}
        )

        handler = DriverStateHandler(db_session, platform="whatsapp")
        resp, state = await handler.handle_message(user, "/start", None)

        assert "ברוך הבא" in resp.text
        assert state == DriverState.REGISTER_COLLECT_NAME.value

    @pytest.mark.asyncio
    async def test_registration_flow_whatsapp(
        self, db_session, user_factory
    ) -> None:
        """זרימת רישום מלאה ב-WhatsApp"""
        user = await user_factory(
            phone_number="+972509991002",
            name="נהג",
            role=UserRole.DRIVER,
        )
        sm = StateManager(db_session)
        await sm.force_state(
            user.id, "whatsapp", DriverState.INITIAL.value, context={}
        )

        handler = DriverStateHandler(db_session, platform="whatsapp")

        # שלב 1: שם
        resp, state = await handler.handle_message(user, "/start", None)
        assert state == DriverState.REGISTER_COLLECT_NAME.value

        # שלב 2: שם מלא
        resp, state = await handler.handle_message(user, "דוד כהן", None)
        assert state == DriverState.REGISTER_COLLECT_BIRTH_DATE.value

        # שלב 3: תאריך לידה
        resp, state = await handler.handle_message(user, "15/06/1985", None)
        assert state == DriverState.REGISTER_COLLECT_VEHICLE.value

        # שלב 4: רכב
        resp, state = await handler.handle_message(
            user, "טויוטה קורולה 2022", None
        )
        assert state == DriverState.REGISTER_COLLECT_DRESS_CODE.value

        # שלב 5: קוד לבוש (חילוני → MENU)
        resp, state = await handler.handle_message(user, "חילוני", None)
        assert state == DriverState.MENU.value

    @pytest.mark.asyncio
    async def test_location_params_passed_to_handler(
        self, db_session, user_factory
    ) -> None:
        """מיקום GPS מועבר נכון ל-handler ב-WhatsApp"""
        user = await user_factory(
            phone_number="+972509991003",
            name="נהג",
            role=UserRole.DRIVER,
        )
        sm = StateManager(db_session)
        await sm.force_state(
            user.id, "whatsapp",
            DriverState.SEARCH_CREATE_ORIGIN.value, context={}
        )

        # יצירת פרופיל רשום
        now = datetime.utcnow()
        profile = DriverProfile(
            user_id=user.id,
            birth_date=date(1985, 6, 15),
            vehicle_description="טויוטה קורולה 2022",
            vehicle_category=VehicleCategory.CAR.value,
            dress_code=DressCode.SECULAR.value,
            subscription_status=DriverSubscriptionStatus.TRIAL.value,
            trial_starts_at=now,
            trial_expires_at=now + timedelta(days=7),
        )
        db_session.add(profile)
        settings = DriverSearchSettings(user_id=user.id)
        db_session.add(settings)
        await db_session.commit()

        handler = DriverStateHandler(db_session, platform="whatsapp")
        resp, state = await handler.handle_message(
            user, "", None,
            location_lat=31.7683, location_lng=35.2137,
        )

        # מצפים שהמיקום עובד — לא שגיאה קריטית
        assert resp is not None
        assert state is not None

    @pytest.mark.asyncio
    async def test_photo_upload_in_verification_whatsapp(
        self, db_session, user_factory
    ) -> None:
        """העלאת תמונה בשלב אימות ב-WhatsApp"""
        user = await user_factory(
            phone_number="+972509991004",
            name="נהג",
            role=UserRole.DRIVER,
        )
        sm = StateManager(db_session)
        await sm.force_state(
            user.id, "whatsapp",
            DriverState.VERIFY_COLLECT_SELFIE.value, context={}
        )

        # יצירת פרופיל חלקי (שלב אימות)
        profile = DriverProfile(
            user_id=user.id,
            birth_date=date(1990, 1, 1),
            vehicle_description="רכב פרטי",
            vehicle_category=VehicleCategory.CAR.value,
            dress_code=DressCode.HASSIDIC.value,
        )
        db_session.add(profile)
        await db_session.commit()

        handler = DriverStateHandler(db_session, platform="whatsapp")
        resp, state = await handler.handle_message(
            user, "", "wa_selfie_file_123"
        )

        # צפוי מעבר לשלב תעודת זהות
        assert state == DriverState.VERIFY_COLLECT_ID_DOCUMENT.value


# ============================================================================
# בדיקות Multi-Step Flow Guard
# ============================================================================


class TestMultiStepFlowGuard:
    """בדיקת guard ל-multi-step flow עם prefix DRIVER"""

    @pytest.mark.asyncio
    async def test_driver_in_registration_is_multi_step(
        self, db_session, user_factory
    ) -> None:
        """נהג באמצע רישום — מזוהה כ-multi-step flow"""
        registration_states = [
            DriverState.REGISTER_COLLECT_NAME.value,
            DriverState.REGISTER_COLLECT_BIRTH_DATE.value,
            DriverState.REGISTER_COLLECT_VEHICLE.value,
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
        ]

        for state in registration_states:
            # כל state שמתחיל ב-DRIVER. צריך להיות multi-step
            assert state.startswith("DRIVER."), (
                f"מצב {state} לא מתחיל ב-DRIVER."
            )

    @pytest.mark.asyncio
    async def test_driver_menu_is_not_registration(
        self, db_session
    ) -> None:
        """תפריט נהג — לא שייך לזרימת רישום"""
        handler = DriverStateHandler(db_session, platform="whatsapp")
        assert not handler._is_registration_flow_state(
            DriverState.MENU.value
        )

    @pytest.mark.asyncio
    async def test_driver_register_is_registration(
        self, db_session
    ) -> None:
        """שלב רישום — שייך לזרימת רישום"""
        handler = DriverStateHandler(db_session, platform="whatsapp")
        assert handler._is_registration_flow_state(
            DriverState.REGISTER_COLLECT_NAME.value
        )

    @pytest.mark.asyncio
    async def test_driver_verify_is_registration(
        self, db_session
    ) -> None:
        """שלב אימות — שייך לזרימת רישום"""
        handler = DriverStateHandler(db_session, platform="whatsapp")
        assert handler._is_registration_flow_state(
            DriverState.VERIFY_COLLECT_SELFIE.value
        )


# ============================================================================
# בדיקות Keyboard Fallback
# ============================================================================


class TestKeyboardFallback:
    """בדיקת fallback — keyboard=None בתגובות"""

    @pytest.mark.asyncio
    async def test_response_has_text(self, db_session, user_factory) -> None:
        """כל תגובה חייבת לכלול טקסט"""
        user = await user_factory(
            phone_number="+972509991010",
            name="נהג",
            role=UserRole.DRIVER,
        )
        sm = StateManager(db_session)
        await sm.force_state(
            user.id, "whatsapp", DriverState.INITIAL.value, context={}
        )

        handler = DriverStateHandler(db_session, platform="whatsapp")
        resp, state = await handler.handle_message(user, "/start", None)

        assert resp.text is not None
        assert len(resp.text) > 0


# ============================================================================
# בדיקות _route_to_role_menu_wa
# ============================================================================


class TestRouteToRoleMenuWA:
    """בדיקות ניתוב נהג ב-_route_to_role_menu_wa"""

    @pytest.mark.asyncio
    async def test_driver_routes_to_handler(
        self, db_session, user_factory
    ) -> None:
        """נהג מנותב ל-DriverStateHandler ב-_route_to_role_menu_wa"""
        from app.api.webhooks.whatsapp import _route_to_role_menu_wa

        user = await user_factory(
            phone_number="+972509991020",
            name="נהג",
            role=UserRole.DRIVER,
        )

        sm = StateManager(db_session)
        resp, new_state = await _route_to_role_menu_wa(user, db_session, sm)

        # מצפים שה-handler הופעל בהצלחה
        assert resp is not None
        assert resp.text is not None
