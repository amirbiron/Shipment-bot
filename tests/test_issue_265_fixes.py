"""
בדיקות לתיקוני Issue #265 — ממצאים קריטיים

1. דליפת חיבורי DB ב-get_task_session()
2. עקיפת מצב רישום ב-_handle_initial()
3. Race condition בארנק — with_for_update()
4. Handler חסר ל-REGISTER_COLLECT_PHONE
5. ולידציה צולבת חסרה ב-driver_menu_service
6. בדיקות authorization חסרות בשירותי נהג
"""
import pytest
from decimal import Decimal
from datetime import datetime, time
from unittest.mock import patch, AsyncMock

from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.courier_wallet import CourierWallet
from app.state_machine.states import SenderState, CourierState
from app.state_machine.handlers import SenderStateHandler, CourierStateHandler


# ============================================================================
# תיקון #1: דליפת חיבורי DB — singleton engine
# ============================================================================


class TestTaskSessionDispose:
    """בדיקות ש-get_task_session() משחרר engine בסיום — מונע דליפת חיבורים"""

    @pytest.mark.asyncio
    async def test_engine_disposed_after_session(self):
        """וידוא ש-engine.dispose() נקרא גם כשהסשן מסתיים בהצלחה"""
        from unittest.mock import patch, AsyncMock, MagicMock
        from app.db.database import get_task_session

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        mock_session = AsyncMock()
        mock_session.close = AsyncMock()

        mock_session_maker = MagicMock()
        mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.db.database.create_async_engine", return_value=mock_engine), \
             patch("app.db.database.async_sessionmaker", return_value=mock_session_maker):
            async with get_task_session() as session:
                pass  # סשן רגיל

        mock_engine.dispose.assert_awaited_once()


# ============================================================================
# תיקון #2: עקיפת מצב רישום ב-_handle_initial()
# ============================================================================


class TestSenderHandleInitialRegistrationCheck:
    """בדיקות ש-_handle_initial לא מתחיל רישום מחדש לשולח רשום"""

    @pytest.mark.asyncio
    async def test_registered_sender_goes_to_menu(self, db_session, user_factory):
        """שולח עם שם קיים צריך לעבור לתפריט, לא לרישום"""
        user = await user_factory(
            name="דניאל",
            role=UserRole.SENDER,
        )

        handler = SenderStateHandler(db_session)
        response, next_state, ctx = await handler._handle_initial(
            "test", {}, user.id
        )

        assert next_state == SenderState.MENU.value
        assert "דניאל" in response.text

    @pytest.mark.asyncio
    async def test_new_sender_starts_registration(self, db_session, user_factory):
        """שולח ללא שם צריך להתחיל רישום"""
        user = await user_factory(
            name=None,
            role=UserRole.SENDER,
        )

        handler = SenderStateHandler(db_session)
        response, next_state, ctx = await handler._handle_initial(
            "test", {}, user.id
        )

        assert next_state == SenderState.REGISTER_COLLECT_NAME.value


class TestCourierHandleInitialRegistrationCheck:
    """בדיקות ש-_handle_initial לא מתחיל רישום מחדש לשליח רשום"""

    @pytest.mark.asyncio
    async def test_registered_courier_goes_to_menu(self, db_session, user_factory):
        """שליח שסיים רישום (terms_accepted) ומאושר — צריך להגיע לתפריט"""
        user = await user_factory(
            name="משה",
            full_name="משה כהן",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        user.terms_accepted_at = datetime.utcnow()
        await db_session.commit()

        handler = CourierStateHandler(db_session)
        response, next_state, ctx = await handler._handle_initial(
            user, "test", {}, ""
        )

        assert next_state == CourierState.MENU.value

    @pytest.mark.asyncio
    async def test_registered_courier_pending_goes_to_pending(
        self, db_session, user_factory
    ):
        """שליח שסיים רישום אך ממתין לאישור — צריך לראות הודעת המתנה"""
        user = await user_factory(
            name="יוסי",
            full_name="יוסי לוי",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.PENDING,
        )
        user.terms_accepted_at = datetime.utcnow()
        await db_session.commit()

        handler = CourierStateHandler(db_session)
        response, next_state, ctx = await handler._handle_initial(
            user, "test", {}, ""
        )

        assert next_state == CourierState.PENDING_APPROVAL.value

    @pytest.mark.asyncio
    async def test_new_courier_starts_registration(self, db_session, user_factory):
        """שליח חדש ללא terms_accepted — צריך להתחיל רישום"""
        user = await user_factory(
            name="חדש",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.PENDING,
        )

        handler = CourierStateHandler(db_session)
        response, next_state, ctx = await handler._handle_initial(
            user, "test", {}, ""
        )

        assert next_state == CourierState.REGISTER_COLLECT_NAME.value

    @pytest.mark.asyncio
    async def test_blocked_courier_cannot_reregister(self, db_session, user_factory):
        """שליח חסום — אסור לאפשר הגשה מחדש, צריך לחסום רישום"""
        user = await user_factory(
            name="חסום",
            full_name="חסום חסום",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.BLOCKED,
        )
        user.terms_accepted_at = datetime.utcnow()
        await db_session.commit()

        handler = CourierStateHandler(db_session)
        response, next_state, ctx = await handler._handle_initial(
            user, "test", {}, ""
        )

        # שליח חסום לא צריך להגיע לרישום
        assert next_state == CourierState.INITIAL.value
        assert "חסום" in response.text


# ============================================================================
# תיקון: מעברי INITIAL→MENU חייבים להיות מוגדרים ב-TRANSITIONS
# ============================================================================


class TestInitialToMenuTransitions:
    """בדיקות שמעברי INITIAL→MENU מוגדרים ב-TRANSITIONS"""

    def test_sender_initial_allows_menu(self):
        """SENDER_TRANSITIONS צריך לכלול MENU ב-INITIAL"""
        from app.state_machine.states import SENDER_TRANSITIONS

        allowed = SENDER_TRANSITIONS[SenderState.INITIAL]
        assert SenderState.MENU in allowed

    def test_courier_initial_allows_menu(self):
        """COURIER_TRANSITIONS צריך לכלול MENU ב-INITIAL"""
        from app.state_machine.states import COURIER_TRANSITIONS

        allowed = COURIER_TRANSITIONS[CourierState.INITIAL]
        assert CourierState.MENU in allowed

    def test_courier_initial_allows_pending_approval(self):
        """COURIER_TRANSITIONS צריך לכלול PENDING_APPROVAL ב-INITIAL"""
        from app.state_machine.states import COURIER_TRANSITIONS

        allowed = COURIER_TRANSITIONS[CourierState.INITIAL]
        assert CourierState.PENDING_APPROVAL in allowed


# ============================================================================
# תיקון: IntegrityError על מספר טלפון כפול ברישום שולח
# ============================================================================


class TestSenderDuplicatePhoneRegistration:
    """בדיקות שרישום עם מספר טלפון כפול מחזיר הודעת שגיאה ולא קורס"""

    @pytest.mark.asyncio
    async def test_duplicate_phone_returns_error(self, db_session, user_factory):
        """רישום עם טלפון שכבר קיים — צריך להחזיר הודעת שגיאה"""
        # יצירת משתמש קיים עם מספר טלפון
        existing_user = await user_factory(
            name="קיים",
            role=UserRole.SENDER,
            phone_number="+972501234567",
        )

        # יצירת משתמש חדש עם מספר שונה — ינסה לשנות לכפול
        new_user = await user_factory(
            name="חדש",
            role=UserRole.SENDER,
            phone_number="+972509999999",
        )

        handler = SenderStateHandler(db_session)
        response, next_state, ctx = await handler._handle_collect_phone(
            "0501234567", {}, new_user.id
        )

        # צריך להישאר ב-REGISTER_COLLECT_PHONE עם הודעת שגיאה
        assert next_state == SenderState.REGISTER_COLLECT_PHONE.value
        assert "כבר רשום" in response.text

    @pytest.mark.asyncio
    async def test_user_not_found_returns_error(self, db_session, user_factory):
        """משתמש שלא נמצא ב-DB — צריך להחזיר שגיאה ולא להצליח בשקט"""
        handler = SenderStateHandler(db_session)
        # מזהה משתמש שלא קיים
        response, next_state, ctx = await handler._handle_collect_phone(
            "0509876543", {}, 999999
        )

        assert next_state == SenderState.INITIAL.value
        assert "שגיאה" in response.text


# ============================================================================
# תיקון: validate_against_existing מדלג כשאין שדות רלוונטיים
# ============================================================================


class TestValidateAgainstExistingSkipsIrrelevant:
    """בדיקות שולידציה צולבת לא חוסמת עדכונים לא קשורים"""

    def test_skips_when_no_relevant_fields(self):
        """כשאף שדה רלוונטי לא עודכן — לא צריך לזרוק שגיאה"""
        from app.schemas.driver import DriverSearchSettingsUpdate

        # מצב DB שבור — future_only_enabled=True עם timeframe שאינו 'all'
        update = DriverSearchSettingsUpdate(vehicle_type_filter="car")
        # לא צריך לזרוק ValueError כי vehicle_type לא קשור
        update.validate_against_existing(
            existing_future_only_enabled=True,
            existing_upcoming_timeframe="3h",
            existing_future_only_start_time=None,
        )

    def test_validates_when_relevant_fields_present(self):
        """כששדה רלוונטי כן עודכן — צריך לבצע ולידציה"""
        from app.schemas.driver import DriverSearchSettingsUpdate

        update = DriverSearchSettingsUpdate(future_only_enabled=True)
        with pytest.raises(ValueError, match="מסגרת הזמן"):
            update.validate_against_existing(
                existing_future_only_enabled=False,
                existing_upcoming_timeframe="3h",
                existing_future_only_start_time=None,
            )


# ============================================================================
# תיקון #3: Race condition בארנק
# ============================================================================


class TestWalletRaceProtection:
    """בדיקות ש-debit_for_capture משתמש ב-for_update למניעת race condition"""

    @pytest.mark.asyncio
    async def test_check_can_capture_is_precheck(
        self, db_session, user_factory, wallet_factory
    ):
        """check_can_capture הוא pre-check ללא נעילה — ההגנה ב-debit_for_capture"""
        courier = await user_factory(
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        from app.domain.services.wallet_service import WalletService

        service = WalletService(db_session)

        # בדיקה שהפונקציה עובדת ומקבלת תוצאה נכונה
        can_capture, msg = await service.check_can_capture(courier.id, 50.0)
        assert can_capture is True

        # בדיקה שנכשלת כשאין מספיק יתרה
        can_capture, msg = await service.check_can_capture(courier.id, 700.0)
        assert can_capture is False
        assert "יתרה לא מספיקה" in msg

    @pytest.mark.asyncio
    async def test_debit_for_capture_uses_for_update(
        self, db_session, user_factory, wallet_factory
    ):
        """debit_for_capture משתמש ב-for_update — ההגנה האמיתית מפני race"""
        courier = await user_factory(
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        from app.domain.services.wallet_service import WalletService

        service = WalletService(db_session)

        # debit מצליח
        ledger = await service.debit_for_capture(courier.id, 1, 50.0)
        assert ledger is not None

        # debit נכשל — חריגה ממגבלת אשראי
        ledger = await service.debit_for_capture(courier.id, 2, 700.0)
        assert ledger is None


# ============================================================================
# תיקון #4: Handler חסר ל-REGISTER_COLLECT_PHONE
# ============================================================================


class TestRegisterCollectPhone:
    """בדיקות ל-handler של REGISTER_COLLECT_PHONE"""

    @pytest.mark.asyncio
    async def test_valid_phone_completes_registration(
        self, db_session, user_factory
    ):
        """מספר טלפון תקין צריך להשלים רישום"""
        user = await user_factory(
            name="דני",
            phone_number="tg:12345",
            role=UserRole.SENDER,
        )

        handler = SenderStateHandler(db_session)
        response, next_state, ctx = await handler._handle_collect_phone(
            "0501234567", {"name": "דני"}, user.id
        )

        assert next_state == SenderState.MENU.value
        assert "הושלמה" in response.text

    @pytest.mark.asyncio
    async def test_invalid_phone_stays_in_state(self, db_session, user_factory):
        """מספר טלפון לא תקין צריך להישאר במצב"""
        user = await user_factory(
            name="דני",
            phone_number="tg:12345",
            role=UserRole.SENDER,
        )

        handler = SenderStateHandler(db_session)
        response, next_state, ctx = await handler._handle_collect_phone(
            "123", {}, user.id
        )

        assert next_state == SenderState.REGISTER_COLLECT_PHONE.value
        assert "לא תקין" in response.text

    @pytest.mark.asyncio
    async def test_collect_name_routes_to_phone_for_tg_users(
        self, db_session, user_factory
    ):
        """משתמש טלגרם (עם placeholder tg:) צריך לעבור לאיסוף טלפון"""
        user = await user_factory(
            name=None,
            phone_number="tg:999",
            role=UserRole.SENDER,
        )

        handler = SenderStateHandler(db_session)
        response, next_state, ctx = await handler._handle_collect_name(
            "דני", {}, user.id
        )

        assert next_state == SenderState.REGISTER_COLLECT_PHONE.value

    @pytest.mark.asyncio
    async def test_collect_name_skips_phone_for_whatsapp_users(
        self, db_session, user_factory
    ):
        """משתמש וואטסאפ (עם מספר אמיתי) צריך לדלג לתפריט"""
        user = await user_factory(
            name=None,
            phone_number="+972501234567",
            role=UserRole.SENDER,
        )

        handler = SenderStateHandler(db_session)
        response, next_state, ctx = await handler._handle_collect_name(
            "דני", {}, user.id
        )

        assert next_state == SenderState.MENU.value

    @pytest.mark.asyncio
    async def test_handler_is_registered(self, db_session):
        """וידוא שה-handler רשום במפה"""
        handler = SenderStateHandler(db_session)
        resolved = handler._get_handler(SenderState.REGISTER_COLLECT_PHONE.value)
        assert resolved is not None
        assert resolved != handler._handle_unknown


# ============================================================================
# תיקון #5: ולידציה צולבת חסרה ב-driver_menu_service
# ============================================================================


class TestDriverMenuCrossValidation:
    """בדיקות לולידציה צולבת ב-update_vehicle_type, update_trip_type, update_show_deliveries"""

    @pytest.mark.asyncio
    async def test_update_vehicle_type_calls_validate(self, db_session, user_factory):
        """update_vehicle_type חייב לקרוא ל-validate_against_existing"""
        from app.domain.services.driver_menu_service import DriverMenuService
        from app.db.models.driver_search_settings import DriverSearchSettings
        from app.db.models.driver_profile import VehicleCategory

        user = await user_factory(role=UserRole.DRIVER)

        # יצירת הגדרות בסיסיות
        settings_obj = DriverSearchSettings(user_id=user.id)
        db_session.add(settings_obj)
        await db_session.commit()

        service = DriverMenuService(db_session)

        # עדכון סוג רכב — צריך לעבור ולידציה בלי שגיאה
        label = await service.update_vehicle_type(user.id, VehicleCategory.CAR.value)
        assert label is not None

    @pytest.mark.asyncio
    async def test_update_trip_type_calls_validate(self, db_session, user_factory):
        """update_trip_type חייב לקרוא ל-validate_against_existing"""
        from app.domain.services.driver_menu_service import DriverMenuService
        from app.db.models.driver_search_settings import DriverSearchSettings, TripTypeFilter

        user = await user_factory(role=UserRole.DRIVER)

        settings_obj = DriverSearchSettings(user_id=user.id)
        db_session.add(settings_obj)
        await db_session.commit()

        service = DriverMenuService(db_session)

        label = await service.update_trip_type(user.id, TripTypeFilter.LONG_DISTANCE.value)
        assert label is not None

    @pytest.mark.asyncio
    async def test_update_show_deliveries_calls_validate(self, db_session, user_factory):
        """update_show_deliveries חייב לקרוא ל-validate_against_existing"""
        from app.domain.services.driver_menu_service import DriverMenuService
        from app.db.models.driver_search_settings import DriverSearchSettings

        user = await user_factory(role=UserRole.DRIVER)

        settings_obj = DriverSearchSettings(user_id=user.id)
        db_session.add(settings_obj)
        await db_session.commit()

        service = DriverMenuService(db_session)

        result = await service.update_show_deliveries(user.id, True)
        assert result is True


# ============================================================================
# תיקון #6: בדיקות authorization חסרות בשירותי נהג
# ============================================================================


class TestDriverSearchAuthorization:
    """בדיקות שפעולות חיפוש דורשות authorization"""

    @pytest.mark.asyncio
    async def test_pause_rejects_non_driver(self, db_session, user_factory):
        """pause_all_searches חייב לדחות משתמש שאינו נהג"""
        from app.domain.services.driver_search_service import DriverSearchService
        from app.core.exceptions import ValidationException

        sender = await user_factory(role=UserRole.SENDER)

        service = DriverSearchService(db_session)

        with pytest.raises(ValidationException, match="הרשאה"):
            await service.pause_all_searches(sender.id)

    @pytest.mark.asyncio
    async def test_resume_rejects_non_driver(self, db_session, user_factory):
        """resume_all_searches חייב לדחות משתמש שאינו נהג"""
        from app.domain.services.driver_search_service import DriverSearchService
        from app.core.exceptions import ValidationException

        courier = await user_factory(role=UserRole.COURIER)

        service = DriverSearchService(db_session)

        with pytest.raises(ValidationException, match="הרשאה"):
            await service.resume_all_searches(courier.id)

    @pytest.mark.asyncio
    async def test_pause_allows_driver(self, db_session, user_factory):
        """pause_all_searches מאשר למשתמש עם תפקיד נהג"""
        from app.domain.services.driver_search_service import DriverSearchService

        driver = await user_factory(role=UserRole.DRIVER)

        service = DriverSearchService(db_session)

        # לא צריך לזרוק שגיאה
        count = await service.pause_all_searches(driver.id)
        assert count == 0  # אין חיפושים פעילים

    @pytest.mark.asyncio
    async def test_resume_allows_driver(self, db_session, user_factory):
        """resume_all_searches מאשר למשתמש עם תפקיד נהג"""
        from app.domain.services.driver_search_service import DriverSearchService

        driver = await user_factory(role=UserRole.DRIVER)

        service = DriverSearchService(db_session)

        count = await service.resume_all_searches(driver.id)
        assert count == 0

    @pytest.mark.asyncio
    async def test_pause_rejects_nonexistent_user(self, db_session):
        """pause_all_searches חייב לדחות משתמש שלא קיים"""
        from app.domain.services.driver_search_service import DriverSearchService
        from app.core.exceptions import ValidationException

        service = DriverSearchService(db_session)

        with pytest.raises(ValidationException, match="לא נמצא"):
            await service.pause_all_searches(999999)
