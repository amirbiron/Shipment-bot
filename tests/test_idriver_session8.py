"""
בדיקות יחידה — iDriver סשן 8: מנוי נהג

בודק:
- DriverSubscriptionService (הפעלת trial, רכישה, בדיקת תקינות, תפוגה)
- DriverStateHandler (זרימת מנוי: MENU → צפייה → רכישה)
- חסימת חיפוש כשמנוי פג
"""
import pytest
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    VehicleCategory,
    DriverSubscriptionStatus,
)
from app.state_machine.states import DriverState
from app.state_machine.driver_handler import DriverStateHandler
from app.domain.services.driver_subscription_service import (
    DriverSubscriptionService,
    TRIAL_DURATION_DAYS,
    SUBSCRIPTION_MONTH_DAYS,
)
from app.core.exceptions import ValidationException, NotFoundException


# ============================================================================
# עזר — יצירת נהג רשום עם פרופיל מלא
# ============================================================================


async def _create_registered_driver(
    db_session,
    user_factory,
    phone: str = "+972505008001",
    subscription_status: str = DriverSubscriptionStatus.TRIAL.value,
    trial_days_remaining: int = 7,
) -> tuple[User, DriverProfile]:
    """יוצר נהג רשום עם פרופיל מלא"""
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
        subscription_status=subscription_status,
        trial_starts_at=now,
        trial_expires_at=now + timedelta(days=trial_days_remaining),
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return user, profile


async def _create_expired_driver(
    db_session,
    user_factory,
    phone: str = "+972505008002",
) -> tuple[User, DriverProfile]:
    """יוצר נהג עם מנוי שפג"""
    user = await user_factory(
        phone_number=phone,
        name="נהג פג",
        full_name="פג תוקף",
        role=UserRole.DRIVER,
        telegram_chat_id=phone.replace("+972", ""),
    )
    now = datetime.utcnow()
    profile = DriverProfile(
        user_id=user.id,
        birth_date=date(1990, 1, 1),
        vehicle_description="מזדה 2023",
        vehicle_category=VehicleCategory.CAR.value,
        dress_code=DressCode.SECULAR.value,
        subscription_status=DriverSubscriptionStatus.EXPIRED.value,
        trial_starts_at=now - timedelta(days=14),
        trial_expires_at=now - timedelta(days=7),
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return user, profile


# ============================================================================
# בדיקות DriverSubscriptionService — שירות מנוי
# ============================================================================


class TestDriverSubscriptionService:
    """בדיקות שירות מנוי נהג"""

    @pytest.mark.asyncio
    async def test_activate_trial(self, db_session, user_factory):
        """הפעלת trial — מגדיר תאריכי התחלה ותפוגה"""
        user = await user_factory(
            phone_number="+972505008010",
            role=UserRole.DRIVER,
            telegram_chat_id="505008010",
        )
        # יצירת פרופיל ללא trial
        profile = DriverProfile(
            user_id=user.id,
            birth_date=date(1990, 1, 1),
            vehicle_description="רכב",
            vehicle_category=VehicleCategory.CAR.value,
            dress_code=DressCode.SECULAR.value,
        )
        db_session.add(profile)
        await db_session.commit()

        service = DriverSubscriptionService(db_session)
        result = await service.activate_trial(user.id)

        assert result.trial_starts_at is not None
        assert result.trial_expires_at is not None
        assert result.subscription_status == DriverSubscriptionStatus.TRIAL.value
        # בדיקת 7 ימים (עם סבילות של דקה)
        delta = result.trial_expires_at - result.trial_starts_at
        assert abs(delta.days - TRIAL_DURATION_DAYS) <= 1

    @pytest.mark.asyncio
    async def test_activate_trial_idempotent(self, db_session, user_factory):
        """הפעלה כפולה של trial לא מאפסת את התאריכים"""
        user, profile = await _create_registered_driver(db_session, user_factory)
        original_start = profile.trial_starts_at

        service = DriverSubscriptionService(db_session)
        result = await service.activate_trial(user.id)

        assert result.trial_starts_at == original_start

    @pytest.mark.asyncio
    async def test_activate_trial_not_found(self, db_session, user_factory):
        """הפעלת trial לנהג לא קיים — NotFoundException"""
        service = DriverSubscriptionService(db_session)
        with pytest.raises(NotFoundException):
            await service.activate_trial(999999)

    @pytest.mark.asyncio
    async def test_is_subscription_active_trial(self, db_session, user_factory):
        """מנוי trial פעיל — מחזיר True"""
        user, _ = await _create_registered_driver(db_session, user_factory)
        service = DriverSubscriptionService(db_session)

        assert await service.is_subscription_active(user.id) is True

    @pytest.mark.asyncio
    async def test_is_subscription_active_expired(self, db_session, user_factory):
        """מנוי שפג — מחזיר False"""
        user, _ = await _create_expired_driver(
            db_session, user_factory, phone="+972505008011"
        )
        service = DriverSubscriptionService(db_session)

        assert await service.is_subscription_active(user.id) is False

    @pytest.mark.asyncio
    async def test_is_subscription_active_trial_expired(self, db_session, user_factory):
        """trial שפג (תאריך בעבר) — מחזיר False"""
        user, profile = await _create_registered_driver(
            db_session, user_factory, phone="+972505008012",
            trial_days_remaining=-1,
        )
        service = DriverSubscriptionService(db_session)

        assert await service.is_subscription_active(user.id) is False

    @pytest.mark.asyncio
    async def test_is_subscription_active_nonexistent(self, db_session):
        """משתמש ללא פרופיל — מחזיר False"""
        service = DriverSubscriptionService(db_session)

        assert await service.is_subscription_active(999999) is False

    @pytest.mark.asyncio
    async def test_purchase_subscription(self, db_session, user_factory):
        """רכישת מנוי חודשי — עדכון סטטוס ותאריכים"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008013"
        )
        service = DriverSubscriptionService(db_session)

        result = await service.purchase_subscription(user.id, months=1)

        assert result.subscription_status == DriverSubscriptionStatus.ACTIVE.value
        assert result.subscription_start_at is not None
        assert result.subscription_expires_at is not None

    @pytest.mark.asyncio
    async def test_purchase_subscription_extends_existing(self, db_session, user_factory):
        """רכישה נוספת — מאריכה מתום המנוי הנוכחי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008014"
        )
        service = DriverSubscriptionService(db_session)

        # רכישה ראשונה
        first = await service.purchase_subscription(user.id, months=1)
        first_end = first.subscription_expires_at

        # רכישה שנייה — מאריכה מהסוף
        second = await service.purchase_subscription(user.id, months=1)
        expected_end = first_end + timedelta(days=SUBSCRIPTION_MONTH_DAYS)

        # סבילות של דקה
        diff = abs((second.subscription_expires_at - expected_end).total_seconds())
        assert diff < 60

    @pytest.mark.asyncio
    async def test_purchase_subscription_invalid_months(self, db_session, user_factory):
        """חודשים לא תקינים — ValidationException"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008015"
        )
        service = DriverSubscriptionService(db_session)

        with pytest.raises(ValidationException):
            await service.purchase_subscription(user.id, months=0)

        with pytest.raises(ValidationException):
            await service.purchase_subscription(user.id, months=13)

    @pytest.mark.asyncio
    async def test_purchase_subscription_not_found(self, db_session):
        """רכישה למשתמש לא קיים — NotFoundException"""
        service = DriverSubscriptionService(db_session)
        with pytest.raises(NotFoundException):
            await service.purchase_subscription(999999, months=1)

    @pytest.mark.asyncio
    async def test_get_subscription_status(self, db_session, user_factory):
        """שליפת סטטוס מנוי — מחזיר dict מפורט"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008016"
        )
        service = DriverSubscriptionService(db_session)

        status = await service.get_subscription_status(user.id)

        assert status["status"] == DriverSubscriptionStatus.TRIAL.value
        assert status["is_active"] is True
        assert status["is_trial"] is True
        assert status["days_remaining"] is not None
        assert status["days_remaining"] >= 6  # 7 ימים minus possible timing

    @pytest.mark.asyncio
    async def test_check_expiring_subscriptions(self, db_session, user_factory):
        """איתור מנויים שעומדים לפוג תוך יום"""
        # נהג עם trial שפוגה מחר
        user = await user_factory(
            phone_number="+972505008017",
            role=UserRole.DRIVER,
            telegram_chat_id="505008017",
        )
        now = datetime.utcnow()
        profile = DriverProfile(
            user_id=user.id,
            birth_date=date(1990, 1, 1),
            vehicle_description="רכב",
            vehicle_category=VehicleCategory.CAR.value,
            dress_code=DressCode.SECULAR.value,
            subscription_status=DriverSubscriptionStatus.TRIAL.value,
            trial_starts_at=now - timedelta(days=6),
            # פוגה תוך 12 שעות (פחות מיום)
            trial_expires_at=now + timedelta(hours=12),
        )
        db_session.add(profile)
        await db_session.commit()

        service = DriverSubscriptionService(db_session)
        expiring = await service.check_expiring_subscriptions()

        assert len(expiring) >= 1
        assert any(p.user_id == user.id for p in expiring)

    @pytest.mark.asyncio
    async def test_expire_lapsed_subscriptions(self, db_session, user_factory):
        """עדכון מנויים שפגו — TRIAL → EXPIRED"""
        user = await user_factory(
            phone_number="+972505008018",
            role=UserRole.DRIVER,
            telegram_chat_id="505008018",
        )
        now = datetime.utcnow()
        profile = DriverProfile(
            user_id=user.id,
            birth_date=date(1990, 1, 1),
            vehicle_description="רכב",
            vehicle_category=VehicleCategory.CAR.value,
            dress_code=DressCode.SECULAR.value,
            subscription_status=DriverSubscriptionStatus.TRIAL.value,
            trial_starts_at=now - timedelta(days=8),
            trial_expires_at=now - timedelta(days=1),  # פג אתמול
        )
        db_session.add(profile)
        await db_session.commit()

        service = DriverSubscriptionService(db_session)
        count = await service.expire_lapsed_subscriptions()

        assert count >= 1

        # בדיקת עדכון סטטוס
        await db_session.refresh(profile)
        assert profile.subscription_status == DriverSubscriptionStatus.EXPIRED.value

    @pytest.mark.asyncio
    async def test_purchase_after_expired(self, db_session, user_factory):
        """רכישה אחרי שמנוי פג — מתחילה מהיום"""
        user, _ = await _create_expired_driver(
            db_session, user_factory, phone="+972505008019"
        )
        service = DriverSubscriptionService(db_session)

        result = await service.purchase_subscription(user.id, months=1)
        now = datetime.utcnow()

        assert result.subscription_status == DriverSubscriptionStatus.ACTIVE.value
        # תאריך התפוגה צריך להיות כ-30 ימים מהיום (לא מתאריך ישן)
        expected_end = now + timedelta(days=SUBSCRIPTION_MONTH_DAYS)
        diff = abs((result.subscription_expires_at - expected_end).total_seconds())
        assert diff < 60


# ============================================================================
# בדיקות Handler — זרימת מנוי ב-DriverStateHandler
# ============================================================================


class TestDriverSubscriptionHandler:
    """בדיקות handler מנוי נהג"""

    @pytest.mark.asyncio
    async def test_menu_shows_subscription_button(self, db_session, user_factory):
        """תפריט ראשי מכיל כפתור מנוי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008020"
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        # סימולציה של כניסה לתפריט
        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)
        await sm.force_state(user.id, "telegram", DriverState.MENU.value, {})

        response, new_state = await handler.handle_message(user, "תפריט")

        assert new_state == DriverState.MENU.value
        # בדיקת כפתור מנוי בתגובה
        assert response.keyboard is not None
        flat_buttons = [btn for row in response.keyboard for btn in row]
        assert any("מנוי" in btn for btn in flat_buttons)

    @pytest.mark.asyncio
    async def test_subscription_view_from_menu(self, db_session, user_factory):
        """לחיצה על כפתור מנוי מהתפריט — מעבר לתצוגת מנוי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008021"
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)
        await sm.force_state(user.id, "telegram", DriverState.MENU.value, {})

        response, new_state = await handler.handle_message(user, "💳 מנוי")

        assert new_state == DriverState.SUBSCRIPTION_VIEW.value
        assert "מנוי" in response.text

    @pytest.mark.asyncio
    async def test_subscription_purchase_flow(self, db_session, user_factory):
        """זרימת רכישה מלאה — בחירת חבילה → תשלום PayBox → צילום מסך"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008022"
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)

        # שלב 1: בחירת חבילה מתצוגת מנוי
        await sm.force_state(user.id, "telegram", DriverState.SUBSCRIPTION_VIEW.value, {})
        response, new_state = await handler.handle_message(user, "📦 3 חודשים")

        assert new_state == DriverState.SUBSCRIPTION_PURCHASE.value
        assert "פייבוקס" in response.text or "תשלום" in response.text
        assert "240" in response.text  # מחיר 3 חודשים

        # שלב 2: שליחת צילום מסך תשלום
        from unittest.mock import AsyncMock, patch

        def _mock_notify():
            return patch(
                "app.domain.services.admin_notification_service.AdminNotificationService.notify_subscription_payment",
                new_callable=AsyncMock,
                return_value=True,
            )

        with _mock_notify() as mock_notify:
            response, new_state = await handler.handle_message(
                user, "", photo_file_id="test_screenshot_123"
            )

        assert new_state == DriverState.MENU.value
        assert "התקבל" in response.text
        assert "אישור הנהלה" in response.text or "אישור" in response.text
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscription_cancel(self, db_session, user_factory):
        """ביטול רכישה — חזרה לתצוגת מנוי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008023"
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)

        # מעבר למסך אישור
        await sm.force_state(
            user.id, "telegram", DriverState.SUBSCRIPTION_PURCHASE.value,
            {"subscription_months": "3"},
        )
        response, new_state = await handler.handle_message(user, "❌ ביטול")

        assert new_state == DriverState.SUBSCRIPTION_VIEW.value

    @pytest.mark.asyncio
    async def test_subscription_back_to_menu(self, db_session, user_factory):
        """חזרה לתפריט מתצוגת מנוי"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008024"
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)
        await sm.force_state(user.id, "telegram", DriverState.SUBSCRIPTION_VIEW.value, {})

        response, new_state = await handler.handle_message(user, "🔙 חזרה לתפריט")

        assert new_state == DriverState.MENU.value

    @pytest.mark.asyncio
    async def test_search_blocked_when_expired(self, db_session, user_factory):
        """חיפוש חסום כשמנוי פג — הודעת שגיאה"""
        user, _ = await _create_expired_driver(
            db_session, user_factory, phone="+972505008025"
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)
        await sm.force_state(user.id, "telegram", DriverState.MENU.value, {})

        response, new_state = await handler.handle_message(user, "פ ים")

        assert new_state == DriverState.MENU.value
        assert "פג תוקף" in response.text

    @pytest.mark.asyncio
    async def test_search_allowed_with_active_trial(self, db_session, user_factory):
        """חיפוש מותר עם trial פעיל — לא נחסם"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008026"
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)
        await sm.force_state(user.id, "telegram", DriverState.MENU.value, {})

        response, new_state = await handler.handle_message(user, "פ ירושלים")

        # לא חסום — או יצליח ליצור חיפוש, או ייכשל בגלל פרסור (לא בגלל מנוי)
        assert "פג תוקף" not in response.text

    @pytest.mark.asyncio
    async def test_trial_last_day_shows_active(self, db_session, user_factory):
        """trial ביום האחרון (פחות מ-24 שעות) — מציג 'יום אחרון' ולא 'פגה'"""
        user, profile = await _create_registered_driver(
            db_session, user_factory, phone="+972505008030",
            # פחות מיום אבל עדיין עתידי
            trial_days_remaining=0,
        )
        # לוודא שה-trial עדיין פעיל — מגדירים תפוגה עם שעות (לא ימים שלמים)
        now = datetime.utcnow()
        profile.trial_expires_at = now + timedelta(hours=10)
        await db_session.commit()

        handler = DriverStateHandler(db_session, platform="telegram")

        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)
        await sm.force_state(user.id, "telegram", DriverState.MENU.value, {})

        response, new_state = await handler.handle_message(user, "💳 מנוי")

        assert new_state == DriverState.SUBSCRIPTION_VIEW.value
        assert "פגה" not in response.text
        assert "יום אחרון" in response.text

    @pytest.mark.asyncio
    async def test_digit_in_freetext_rejected(self, db_session, user_factory):
        """טקסט חופשי עם ספרות (כמו '16') — לא מתפרש כחבילה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008031"
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)
        await sm.force_state(user.id, "telegram", DriverState.SUBSCRIPTION_VIEW.value, {})

        # "16" לא צריך להתפרש כ-6 חודשים
        response, new_state = await handler.handle_message(user, "16")

        assert new_state == DriverState.SUBSCRIPTION_VIEW.value
        assert "לא זיהיתי" in response.text

    @pytest.mark.asyncio
    async def test_invalid_subscription_choice(self, db_session, user_factory):
        """בחירת חבילה לא תקינה — הודעת שגיאה"""
        user, _ = await _create_registered_driver(
            db_session, user_factory, phone="+972505008027"
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)
        await sm.force_state(user.id, "telegram", DriverState.SUBSCRIPTION_VIEW.value, {})

        response, new_state = await handler.handle_message(user, "אפשרות לא קיימת")

        assert new_state == DriverState.SUBSCRIPTION_VIEW.value
        assert "לא זיהיתי" in response.text


# ============================================================================
# בדיקות פורמט סטטוס מנוי (DriverMenuService)
# ============================================================================


class TestSubscriptionStatusFormat:
    """בדיקות פורמט סטטוס מנוי לתצוגה"""

    @pytest.mark.unit
    def test_format_trial_with_date(self):
        """פורמט trial עם תאריך תפוגה"""
        from app.domain.services.driver_menu_service import DriverMenuService

        profile = DriverProfile(
            id=1, user_id=1,
            birth_date=date(1990, 1, 1),
            vehicle_description="רכב",
            vehicle_category=VehicleCategory.CAR.value,
            dress_code=DressCode.SECULAR.value,
            subscription_status=DriverSubscriptionStatus.TRIAL.value,
            trial_expires_at=datetime(2026, 3, 11),
        )
        result = DriverMenuService._format_subscription_status(profile)
        assert "שבוע ניסיון" in result
        assert "11/03/2026" in result

    @pytest.mark.unit
    def test_format_active_subscription(self):
        """פורמט מנוי פעיל"""
        from app.domain.services.driver_menu_service import DriverMenuService

        profile = DriverProfile(
            id=1, user_id=1,
            birth_date=date(1990, 1, 1),
            vehicle_description="רכב",
            vehicle_category=VehicleCategory.CAR.value,
            dress_code=DressCode.SECULAR.value,
            subscription_status=DriverSubscriptionStatus.ACTIVE.value,
            subscription_expires_at=datetime(2026, 6, 1),
        )
        result = DriverMenuService._format_subscription_status(profile)
        assert "מנוי פעיל" in result

    @pytest.mark.unit
    def test_format_expired_subscription(self):
        """פורמט מנוי שפג"""
        from app.domain.services.driver_menu_service import DriverMenuService

        profile = DriverProfile(
            id=1, user_id=1,
            birth_date=date(1990, 1, 1),
            vehicle_description="רכב",
            vehicle_category=VehicleCategory.CAR.value,
            dress_code=DressCode.SECULAR.value,
            subscription_status=DriverSubscriptionStatus.EXPIRED.value,
        )
        result = DriverMenuService._format_subscription_status(profile)
        assert "המנוי פג" in result
