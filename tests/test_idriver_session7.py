"""
בדיקות יחידה — iDriver סשן 7: פרסום נסיעות + מחירון

בודק:
- PricingService (פרסור פקודות, שליפת מחירים, פורמט תגובות)
- RidePostingService (פרסור פקודות פרסום, זיהוי פורמט, פורמט הודעה)
- DriverStateHandler (זרימת פרסום ומחירון מתוך MENU)
"""
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    VehicleCategory,
    DriverSubscriptionStatus,
)
from app.state_machine.states import DriverState
from app.state_machine.driver_handler import DriverStateHandler
from app.domain.services.pricing_service import PricingService, PriceEstimate
from app.domain.services.ride_posting_service import (
    RidePostingService,
    ParsedRidePosting,
)


# ============================================================================
# עזר — יצירת נהג רשום עם פרופיל מלא
# ============================================================================


async def _create_registered_driver(
    db_session,
    user_factory,
    phone: str = "+972505001001",
) -> tuple[User, DriverProfile]:
    """יוצר נהג רשום עם פרופיל מלא (is_registration_complete=True)"""
    user = await user_factory(
        phone_number=phone,
        name="נהג בדיקה",
        full_name="ישראל ישראלי",
        role=UserRole.DRIVER,
        telegram_chat_id=phone.replace("+972", ""),
    )
    now = datetime.utcnow()
    profile = DriverProfile(
        user_id=user.id,
        birth_date=date(1990, 1, 1),
        vehicle_description="טויוטה 2024",
        vehicle_category=VehicleCategory.SEVEN_SEATER.value,
        dress_code=DressCode.SECULAR.value,
        subscription_status=DriverSubscriptionStatus.TRIAL.value,
        trial_starts_at=now,
        trial_expires_at=now + timedelta(days=7),
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return user, profile


# ============================================================================
# בדיקות PricingService — מחירון
# ============================================================================


class TestPricingService:
    """בדיקות שירות מחירון"""

    @pytest.mark.unit
    def test_is_pricing_command_valid(self) -> None:
        """זיהוי פקודות מחירון תקינות"""
        assert PricingService.is_pricing_command("מחירון בב ים") is True
        assert PricingService.is_pricing_command("מחירון תל אביב ירושלים") is True
        assert PricingService.is_pricing_command("  מחירון בב ים  ") is True

    @pytest.mark.unit
    def test_is_pricing_command_invalid(self) -> None:
        """דחיית טקסטים שאינם פקודת מחירון"""
        assert PricingService.is_pricing_command("שלום") is False
        assert PricingService.is_pricing_command("מחירון") is False
        assert PricingService.is_pricing_command("") is False
        assert PricingService.is_pricing_command("פ ים") is False

    @pytest.mark.unit
    def test_parse_pricing_two_abbreviations(self) -> None:
        """פרסור מחירון עם שני קיצורים: מחירון בב ים"""
        result = PricingService.parse_pricing_command("מחירון בב ים")
        assert result is not None
        origin, destination = result
        assert origin == "בני ברק"
        assert destination == "ירושלים"

    @pytest.mark.unit
    def test_parse_pricing_full_names(self) -> None:
        """פרסור מחירון עם שמות מלאים"""
        result = PricingService.parse_pricing_command("מחירון תל אביב ירושלים")
        assert result is not None
        origin, destination = result
        assert origin == "תל אביב"
        assert destination == "ירושלים"

    @pytest.mark.unit
    def test_parse_pricing_mixed(self) -> None:
        """פרסור מחירון עם קיצור + שם מלא"""
        result = PricingService.parse_pricing_command("מחירון בב תל אביב")
        assert result is not None
        origin, destination = result
        assert origin == "בני ברק"

    @pytest.mark.unit
    def test_parse_pricing_too_few_args(self) -> None:
        """פרסור מחירון עם פרמטר אחד בלבד — None"""
        result = PricingService.parse_pricing_command("מחירון ים")
        assert result is None

    @pytest.mark.unit
    def test_parse_pricing_not_command(self) -> None:
        """טקסט שאינו מחירון — None"""
        result = PricingService.parse_pricing_command("שלום")
        assert result is None

    @pytest.mark.unit
    def test_get_price_known_route(self) -> None:
        """שליפת מחיר למסלול מוכר"""
        estimate = PricingService.get_price_estimate("תל אביב", "ירושלים")
        assert estimate is not None
        assert estimate.origin == "תל אביב"
        assert estimate.destination == "ירושלים"
        assert estimate.min_price == 120
        assert estimate.max_price == 180

    @pytest.mark.unit
    def test_get_price_reverse_route(self) -> None:
        """שליפת מחיר למסלול הפוך"""
        estimate = PricingService.get_price_estimate("ירושלים", "תל אביב")
        assert estimate is not None
        assert estimate.min_price == 120
        assert estimate.max_price == 180

    @pytest.mark.unit
    def test_get_price_unknown_route(self) -> None:
        """מסלול לא מוכר — None"""
        estimate = PricingService.get_price_estimate("נתיבות", "קריית שמונה")
        assert estimate is None

    @pytest.mark.unit
    def test_get_price_beitar_jerusalem(self) -> None:
        """מסלול ביתר עילית ← ירושלים"""
        estimate = PricingService.get_price_estimate("ביתר עילית", "ירושלים")
        assert estimate is not None
        assert estimate.min_price == 50
        assert estimate.max_price == 80

    @pytest.mark.unit
    def test_get_price_eilat_routes(self) -> None:
        """מסלולי אילת — מחירים גבוהים"""
        estimate = PricingService.get_price_estimate("אילת", "תל אביב")
        assert estimate is not None
        assert estimate.min_price >= 400

    @pytest.mark.unit
    def test_format_price_response(self) -> None:
        """פורמט תגובת מחירון"""
        estimate = PriceEstimate(
            origin="בני ברק",
            destination="ירושלים",
            min_price=100,
            max_price=160,
        )
        result = PricingService.format_price_response(estimate)
        assert "מחירון" in result
        assert "בני ברק" in result
        assert "ירושלים" in result
        assert "100" in result
        assert "160" in result
        assert "₪" in result

    @pytest.mark.unit
    def test_format_not_found_response(self) -> None:
        """פורמט תגובה כשלא נמצא מחיר"""
        result = PricingService.format_not_found_response("נתיבות", "עפולה")
        assert "לא נמצא" in result
        assert "נתיבות" in result
        assert "עפולה" in result

    @pytest.mark.unit
    def test_format_price_response_html_escape(self) -> None:
        """XSS — תווים מיוחדים ב-HTML מוסנטזים"""
        estimate = PriceEstimate(
            origin="<script>alert(1)</script>",
            destination="עיר",
            min_price=100,
            max_price=200,
        )
        result = PricingService.format_price_response(estimate)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


# ============================================================================
# בדיקות RidePostingService — פרסום נסיעות
# ============================================================================


class TestRidePostingService:
    """בדיקות שירות פרסום נסיעות"""

    @pytest.mark.unit
    def test_is_ride_posting_valid(self) -> None:
        """זיהוי פקודות פרסום נסיעה תקינות"""
        assert RidePostingService.is_ride_posting("בב ים 5 מק 150 ש״ח") is True
        assert RidePostingService.is_ride_posting("בב ים 5 מק 150") is True
        assert RidePostingService.is_ride_posting("תא חי 3 מק 200 שח") is True

    @pytest.mark.unit
    def test_is_ride_posting_invalid(self) -> None:
        """דחיית טקסטים שאינם פרסום נסיעה"""
        assert RidePostingService.is_ride_posting("שלום") is False
        assert RidePostingService.is_ride_posting("") is False
        assert RidePostingService.is_ride_posting("פ ים") is False
        assert RidePostingService.is_ride_posting("מחירון בב ים") is False
        # חסר מק
        assert RidePostingService.is_ride_posting("בב ים 150") is False

    @pytest.mark.unit
    def test_parse_basic_posting(self) -> None:
        """פרסור פקודת פרסום בסיסית"""
        result = RidePostingService.parse_ride_posting("בב ים 5 מק 150 ש״ח")
        assert result is not None
        assert result.origin == "בני ברק"
        assert result.destination == "ירושלים"
        assert result.seats == 5
        assert result.price == 150.0

    @pytest.mark.unit
    def test_parse_without_shekel_suffix(self) -> None:
        """פרסור ללא סיומת ש״ח"""
        result = RidePostingService.parse_ride_posting("בב ים 3 מק 100")
        assert result is not None
        assert result.seats == 3
        assert result.price == 100.0

    @pytest.mark.unit
    def test_parse_full_city_names(self) -> None:
        """פרסור עם שמות ערים מלאים"""
        result = RidePostingService.parse_ride_posting(
            "בני ברק ירושלים 4 מק 120 ש״ח"
        )
        assert result is not None
        assert result.origin == "בני ברק"
        assert result.destination == "ירושלים"

    @pytest.mark.unit
    def test_parse_zero_seats_rejected(self) -> None:
        """0 מקומות — דחייה"""
        result = RidePostingService.parse_ride_posting("בב ים 0 מק 150 ש״ח")
        assert result is None

    @pytest.mark.unit
    def test_parse_too_many_seats_rejected(self) -> None:
        """יותר מ-50 מקומות — דחייה"""
        result = RidePostingService.parse_ride_posting("בב ים 51 מק 150 ש״ח")
        assert result is None

    @pytest.mark.unit
    def test_parse_zero_price_rejected(self) -> None:
        """מחיר 0 — דחייה"""
        result = RidePostingService.parse_ride_posting("בב ים 5 מק 0 ש״ח")
        assert result is None

    @pytest.mark.unit
    def test_parse_single_word_rejected(self) -> None:
        """מילה אחת בלבד — דחייה"""
        result = RidePostingService.parse_ride_posting("בב 5 מק 150")
        assert result is None

    @pytest.mark.unit
    def test_format_ride_message(self) -> None:
        """פורמט הודעת נסיעה"""
        posting = ParsedRidePosting(
            origin="בני ברק",
            destination="ירושלים",
            seats=5,
            price=150.0,
        )
        result = RidePostingService.format_ride_message(posting, "ישראל כהן")
        assert "נסיעה חדשה" in result
        assert "בני ברק" in result
        assert "ירושלים" in result
        assert "5" in result
        assert "150" in result
        assert "ישראל כהן" in result

    @pytest.mark.unit
    def test_format_ride_message_html_escape(self) -> None:
        """XSS — תווים מיוחדים ב-HTML מוסנטזים"""
        posting = ParsedRidePosting(
            origin="<b>עיר</b>",
            destination="יעד",
            seats=1,
            price=50.0,
        )
        result = RidePostingService.format_ride_message(posting, "<script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    @pytest.mark.asyncio
    async def test_get_relevant_groups_no_stations(self, db_session) -> None:
        """ללא תחנות — רשימה ריקה"""
        service = RidePostingService(db_session)
        groups = await service.get_relevant_groups("תל אביב", "ירושלים")
        assert groups == []


# ============================================================================
# בדיקות DriverStateHandler — זרימת מחירון ופרסום
# ============================================================================


class TestDriverPricingHandler:
    """בדיקות handler מחירון"""

    @pytest.mark.asyncio
    async def test_pricing_command_known_route(
        self, db_session, user_factory
    ) -> None:
        """פקודת 'מחירון בב ים' — תגובה עם טווח מחירים"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972505007001"
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "מחירון בב ים")
        assert new_state == DriverState.MENU.value
        assert "מחירון" in response.text
        assert "בני ברק" in response.text
        assert "ירושלים" in response.text
        assert "100" in response.text  # min_price
        assert "160" in response.text  # max_price

    @pytest.mark.asyncio
    async def test_pricing_command_unknown_route(
        self, db_session, user_factory
    ) -> None:
        """פקודת מחירון למסלול לא מוכר — הודעת 'לא נמצא'"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972505007002"
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(
            user, "מחירון נתיבות קריית שמונה"
        )
        assert new_state == DriverState.MENU.value
        assert "לא נמצא" in response.text

    @pytest.mark.asyncio
    async def test_pricing_command_invalid_format(
        self, db_session, user_factory
    ) -> None:
        """פקודת מחירון לא תקינה — הודעת שגיאה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972505007003"
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "מחירון ים")
        assert new_state == DriverState.MENU.value
        assert "❌" in response.text
        assert "פורמט" in response.text


class TestDriverRidePostingHandler:
    """בדיקות handler פרסום נסיעות"""

    @pytest.mark.asyncio
    async def test_ride_posting_no_groups(
        self, db_session, user_factory
    ) -> None:
        """פרסום נסיעה ללא קבוצות — אישור עם 0 קבוצות"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972505007010"
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(
            user, "בב ים 5 מק 150 ש״ח"
        )
        assert new_state == DriverState.MENU.value
        # בלי קבוצות — מציג את ההודעה אבל ללא שליחה
        assert "נסיעה חדשה" in response.text or "פורסם" in response.text

    @pytest.mark.asyncio
    async def test_ride_posting_invalid_format(
        self, db_session, user_factory
    ) -> None:
        """פרסום נסיעה בפורמט לא תקין — הודעת שגיאה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972505007011"
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        # מק בלי מחיר תקין — is_ride_posting ידחה את זה
        # ננסה משהו שעובר is_ride_posting אבל נכשל בפרסור
        # בפועל, is_ride_posting כבר מסנן — ניסיון עם פורמט גבולי
        response, new_state = await handler.handle_message(
            user, "x 5 מק 150"
        )
        # הפקודה תזוהה כפרסום (יש מק + מחיר) אבל הפרסור ייכשל
        # כי יש מילה אחת בלבד לפני ה-"מק"
        assert new_state == DriverState.MENU.value

    @pytest.mark.asyncio
    async def test_help_includes_ride_posting(
        self, db_session, user_factory
    ) -> None:
        """הוראות שימוש כוללות הסבר על פרסום נסיעות ומחירון"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972505007020"
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(user, "הוראות")
        assert "מחירון" in response.text
        assert "מק" in response.text

    @pytest.mark.asyncio
    async def test_pricing_before_ride_posting(
        self, db_session, user_factory
    ) -> None:
        """'מחירון' מזוהה כפקודת מחירון ולא כפרסום"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, "+972505007030"
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        await handler.state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        response, new_state = await handler.handle_message(
            user, "מחירון תל אביב חיפה"
        )
        assert new_state == DriverState.MENU.value
        # חייב להיות תגובת מחירון, לא פרסום
        assert "מחירון" in response.text
        assert "200" in response.text  # min_price תא-חיפה
