"""
בדיקות יחידה — iDriver סשן 2: זרימת רישום נהג

בודק:
- DriverRegistrationService (שמירת שם, תאריך לידה, רכב, קוד לבוש)
- DriverStateHandler (מעברי מצבים ותגובות)
- ולידציות (פורמט תאריך, טווח גיל, תיאור רכב, קוד לבוש)
- ניתוב לפי קוד לבוש (זרם חרדי → אימות, אחר → תפריט)
"""
import pytest
from datetime import date, timedelta

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    DriverVerificationStatus,
    DriverSubscriptionStatus,
)
from app.state_machine.states import DriverState
from app.domain.services.driver_registration_service import (
    DriverRegistrationService,
    HAREDI_DRESS_CODES,
)
from app.state_machine.driver_handler import (
    DriverStateHandler,
    DRESS_CODE_LABELS,
    DRESS_CODE_BY_LABEL,
)
from app.state_machine.manager import StateManager
from app.core.exceptions import ValidationException, NotFoundException


# ============================================================================
# בדיקות DriverRegistrationService
# ============================================================================


class TestDriverRegistrationServiceName:
    """בדיקות שמירת שם"""

    @pytest.mark.asyncio
    async def test_save_name_valid(self, db_session, user_factory) -> None:
        """שמירת שם תקין"""
        user = await user_factory(
            phone_number="+972501112222",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        saved = await service.save_name(user.id, "ישראל ישראלי")
        assert saved == "ישראל ישראלי"

        await db_session.refresh(user)
        assert user.full_name == "ישראל ישראלי"

    @pytest.mark.asyncio
    async def test_save_name_trims_whitespace(self, db_session, user_factory) -> None:
        """סניטציה — חיתוך רווחים"""
        user = await user_factory(
            phone_number="+972501112223",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        saved = await service.save_name(user.id, "  יוסי כהן  ")
        assert saved == "יוסי כהן"

    @pytest.mark.asyncio
    async def test_save_name_too_short(self, db_session, user_factory) -> None:
        """שם קצר מדי"""
        user = await user_factory(
            phone_number="+972501112224",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        with pytest.raises(ValidationException):
            await service.save_name(user.id, "א")

    @pytest.mark.asyncio
    async def test_save_name_user_not_found(self, db_session) -> None:
        """משתמש לא קיים"""
        service = DriverRegistrationService(db_session)
        with pytest.raises(NotFoundException):
            await service.save_name(999999, "שם תקין")


class TestDriverRegistrationServiceBirthDate:
    """בדיקות תאריך לידה"""

    @pytest.mark.asyncio
    async def test_save_birth_date_valid(self, db_session, user_factory) -> None:
        """תאריך לידה תקין"""
        user = await user_factory(
            phone_number="+972501113333",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        birth_date, age = await service.save_birth_date(user.id, "15/05/1990")
        assert birth_date == date(1990, 5, 15)
        assert age >= 35  # נכון ל-2026

    @pytest.mark.asyncio
    async def test_save_birth_date_invalid_format(self, db_session, user_factory) -> None:
        """פורמט תאריך שגוי"""
        user = await user_factory(
            phone_number="+972501113334",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        with pytest.raises(ValidationException, match="פורמט תאריך"):
            await service.save_birth_date(user.id, "1990-05-15")

    @pytest.mark.asyncio
    async def test_save_birth_date_too_young(self, db_session, user_factory) -> None:
        """גיל מתחת ל-16"""
        user = await user_factory(
            phone_number="+972501113335",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        young_date = date.today() - timedelta(days=365 * 10)
        date_str = young_date.strftime("%d/%m/%Y")
        with pytest.raises(ValidationException, match="16"):
            await service.save_birth_date(user.id, date_str)

    @pytest.mark.asyncio
    async def test_save_birth_date_too_old(self, db_session, user_factory) -> None:
        """גיל מעל 99"""
        user = await user_factory(
            phone_number="+972501113336",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        with pytest.raises(ValidationException, match="99"):
            await service.save_birth_date(user.id, "01/01/1920")

    @pytest.mark.asyncio
    async def test_save_birth_date_exactly_16(self, db_session, user_factory) -> None:
        """גיל 16 בדיוק"""
        user = await user_factory(
            phone_number="+972501113337",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        today = date.today()
        try:
            sixteen_ago = today.replace(year=today.year - 16)
        except ValueError:
            # 29 בפברואר בשנה מעוברת — שנת היעד לא מעוברת
            sixteen_ago = today.replace(year=today.year - 16, day=28)
        date_str = sixteen_ago.strftime("%d/%m/%Y")
        birth_date, age = await service.save_birth_date(user.id, date_str)
        assert age == 16


class TestDriverRegistrationServiceVehicle:
    """בדיקות תיאור רכב"""

    @pytest.mark.asyncio
    async def test_save_vehicle_valid(self, db_session, user_factory) -> None:
        """תיאור רכב תקין"""
        user = await user_factory(
            phone_number="+972501114444",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        saved = await service.save_vehicle(user.id, "סיינה 2025 חדישה")
        assert saved == "סיינה 2025 חדישה"

    @pytest.mark.asyncio
    async def test_save_vehicle_empty(self, db_session, user_factory) -> None:
        """תיאור רכב ריק"""
        user = await user_factory(
            phone_number="+972501114445",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        with pytest.raises(ValidationException, match="שדה חובה"):
            await service.save_vehicle(user.id, "   ")

    @pytest.mark.asyncio
    async def test_save_vehicle_too_long(self, db_session, user_factory) -> None:
        """תיאור רכב ארוך מדי"""
        user = await user_factory(
            phone_number="+972501114446",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        with pytest.raises(ValidationException, match="200"):
            await service.save_vehicle(user.id, "א" * 201)


class TestDriverRegistrationServiceDressCode:
    """בדיקות קוד לבוש"""

    @pytest.mark.asyncio
    async def test_save_dress_code_secular(self, db_session, user_factory) -> None:
        """שמירת קוד לבוש חילוני — לא צריך אימות"""
        user = await user_factory(
            phone_number="+972501115555",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        saved_code, needs_verification = await service.save_dress_code(
            user.id, DressCode.SECULAR.value
        )
        assert saved_code == "secular"
        assert needs_verification is False

    @pytest.mark.asyncio
    async def test_save_dress_code_hassidic(self, db_session, user_factory) -> None:
        """שמירת קוד לבוש חסידי — צריך אימות"""
        user = await user_factory(
            phone_number="+972501115556",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        saved_code, needs_verification = await service.save_dress_code(
            user.id, DressCode.HASSIDIC.value
        )
        assert saved_code == "hassidic"
        assert needs_verification is True

    @pytest.mark.asyncio
    async def test_save_dress_code_invalid(self, db_session, user_factory) -> None:
        """קוד לבוש לא תקין"""
        user = await user_factory(
            phone_number="+972501115557",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        with pytest.raises(ValidationException, match="קוד לבוש"):
            await service.save_dress_code(user.id, "alien")

    @pytest.mark.asyncio
    async def test_save_dress_code_sets_trial(self, db_session, user_factory) -> None:
        """שמירת קוד לבוש מפעילה תקופת ניסיון"""
        user = await user_factory(
            phone_number="+972501115558",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverRegistrationService(db_session)
        await service.save_dress_code(user.id, DressCode.SECULAR.value)

        from sqlalchemy import select

        result = await db_session.execute(
            select(DriverProfile).where(DriverProfile.user_id == user.id)
        )
        profile = result.scalar_one()
        assert profile.trial_starts_at is not None
        assert profile.trial_expires_at is not None

    @pytest.mark.unit
    def test_requires_verification_haredi_codes(self) -> None:
        """כל קודי לבוש חרדיים דורשים אימות"""
        assert DriverRegistrationService.requires_verification("hassidic") is True
        assert DriverRegistrationService.requires_verification("ultra_orthodox") is True
        assert DriverRegistrationService.requires_verification("modern_orthodox") is True

    @pytest.mark.unit
    def test_requires_verification_non_haredi_codes(self) -> None:
        """קודי לבוש לא חרדיים לא דורשים אימות"""
        assert DriverRegistrationService.requires_verification("religious_elegant") is False
        assert DriverRegistrationService.requires_verification("mixed") is False
        assert DriverRegistrationService.requires_verification("secular") is False


# ============================================================================
# בדיקות DRESS_CODE_LABELS
# ============================================================================


class TestDressCodeLabels:
    """בדיקות מיפוי טקסט כפתורים"""

    @pytest.mark.unit
    def test_all_dress_codes_have_labels(self) -> None:
        """כל ערכי DressCode חייבים להיות ממופים לתווית"""
        for code in DressCode:
            assert code.value in DRESS_CODE_LABELS, f"חסר label ל-{code.value}"

    @pytest.mark.unit
    def test_reverse_mapping_complete(self) -> None:
        """המיפוי ההפוך חייב לכסות את כל התוויות"""
        assert len(DRESS_CODE_BY_LABEL) == len(DRESS_CODE_LABELS)
        for label, value in DRESS_CODE_BY_LABEL.items():
            assert DRESS_CODE_LABELS[value] == label

    @pytest.mark.unit
    def test_haredi_labels(self) -> None:
        """תוויות חרדיות"""
        assert DRESS_CODE_LABELS["hassidic"] == "חסיד שחור לבן"
        assert DRESS_CODE_LABELS["ultra_orthodox"] == "חרדי שחור לבן"
        assert DRESS_CODE_LABELS["modern_orthodox"] == "חרדי מודרני"


# ============================================================================
# בדיקות DriverStateHandler
# ============================================================================


class TestDriverStateHandlerInitial:
    """בדיקות מצב ראשוני"""

    @pytest.mark.asyncio
    async def test_initial_state_shows_welcome(self, db_session, user_factory) -> None:
        """מצב INITIAL מציג הודעת ברוכים הבאים ועובר לאיסוף שם"""
        user = await user_factory(
            phone_number="+972502221111",
            name="נהג חדש",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.INITIAL.value, context={}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "/start", None)

        assert "ברוך הבא" in response.text
        assert "שם המלא" in response.text
        assert new_state == DriverState.REGISTER_COLLECT_NAME.value


class TestDriverStateHandlerName:
    """בדיקות שלב איסוף שם"""

    @pytest.mark.asyncio
    async def test_valid_name_proceeds_to_birth_date(
        self, db_session, user_factory
    ) -> None:
        """שם תקין עובר לשלב תאריך לידה"""
        user = await user_factory(
            phone_number="+972502222111",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.REGISTER_COLLECT_NAME.value, context={}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "ישראל ישראלי", None
        )

        assert "ישראל ישראלי" in response.text
        assert "תאריך הלידה" in response.text
        assert new_state == DriverState.REGISTER_COLLECT_BIRTH_DATE.value

    @pytest.mark.asyncio
    async def test_invalid_name_stays(self, db_session, user_factory) -> None:
        """שם לא תקין נשאר באותו מצב"""
        user = await user_factory(
            phone_number="+972502222112",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.REGISTER_COLLECT_NAME.value, context={}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "א", None)

        assert "❌" in response.text
        assert new_state == DriverState.REGISTER_COLLECT_NAME.value


class TestDriverStateHandlerBirthDate:
    """בדיקות שלב תאריך לידה"""

    @pytest.mark.asyncio
    async def test_valid_date_proceeds_to_vehicle(
        self, db_session, user_factory
    ) -> None:
        """תאריך תקין עובר לשלב רכב"""
        user = await user_factory(
            phone_number="+972502223111",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_BIRTH_DATE.value,
            context={"reg_name": "ישראל ישראלי"},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "15/05/1990", None
        )

        assert "גיל" in response.text
        assert "סוג הרכב" in response.text
        assert new_state == DriverState.REGISTER_COLLECT_VEHICLE.value

    @pytest.mark.asyncio
    async def test_invalid_format_stays(self, db_session, user_factory) -> None:
        """פורמט שגוי נשאר באותו מצב"""
        user = await user_factory(
            phone_number="+972502223112",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_BIRTH_DATE.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "not-a-date", None
        )

        assert "❌" in response.text
        assert new_state == DriverState.REGISTER_COLLECT_BIRTH_DATE.value


class TestDriverStateHandlerVehicle:
    """בדיקות שלב רכב"""

    @pytest.mark.asyncio
    async def test_valid_vehicle_proceeds_to_dress_code(
        self, db_session, user_factory
    ) -> None:
        """תיאור רכב תקין עובר לשלב קוד לבוש"""
        user = await user_factory(
            phone_number="+972502224111",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_VEHICLE.value,
            context={"reg_name": "ישראל", "reg_age": "35"},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "סיינה 2025 חדישה", None
        )

        assert "סיינה 2025 חדישה" in response.text
        assert "קוד הלבוש" in response.text
        assert response.keyboard is not None
        assert new_state == DriverState.REGISTER_COLLECT_DRESS_CODE.value

    @pytest.mark.asyncio
    async def test_empty_vehicle_stays(self, db_session, user_factory) -> None:
        """תיאור ריק נשאר באותו מצב"""
        user = await user_factory(
            phone_number="+972502224112",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_VEHICLE.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "   ", None)

        assert "❌" in response.text
        assert new_state == DriverState.REGISTER_COLLECT_VEHICLE.value


class TestDriverStateHandlerDressCode:
    """בדיקות שלב קוד לבוש"""

    @pytest.mark.asyncio
    async def test_secular_goes_to_menu(self, db_session, user_factory) -> None:
        """בחירת 'חילוני' עוברת לתפריט ראשי"""
        user = await user_factory(
            phone_number="+972502225111",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            context={"reg_name": "ישראל", "reg_age": "35", "reg_vehicle": "טויוטה"},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "חילוני", None)

        assert "הרישום הושלם" in response.text
        assert new_state == DriverState.MENU.value

    @pytest.mark.asyncio
    async def test_hassidic_goes_to_verification(
        self, db_session, user_factory
    ) -> None:
        """בחירת 'חסיד שחור לבן' עוברת לאימות"""
        user = await user_factory(
            phone_number="+972502225112",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            context={"reg_name": "ישראל", "reg_age": "35", "reg_vehicle": "טויוטה"},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "חסיד שחור לבן", None
        )

        assert "אימות" in response.text
        assert new_state == DriverState.VERIFY_COLLECT_SELFIE.value

    @pytest.mark.asyncio
    async def test_ultra_orthodox_goes_to_verification(
        self, db_session, user_factory
    ) -> None:
        """בחירת 'חרדי שחור לבן' עוברת לאימות"""
        user = await user_factory(
            phone_number="+972502225113",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            context={"reg_name": "ישראל", "reg_age": "35", "reg_vehicle": "טויוטה"},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "חרדי שחור לבן", None
        )

        assert new_state == DriverState.VERIFY_COLLECT_SELFIE.value

    @pytest.mark.asyncio
    async def test_modern_orthodox_goes_to_verification(
        self, db_session, user_factory
    ) -> None:
        """בחירת 'חרדי מודרני' עוברת לאימות"""
        user = await user_factory(
            phone_number="+972502225114",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            context={"reg_name": "ישראל", "reg_age": "35", "reg_vehicle": "טויוטה"},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "חרדי מודרני", None
        )

        assert new_state == DriverState.VERIFY_COLLECT_SELFIE.value

    @pytest.mark.asyncio
    async def test_religious_elegant_goes_to_menu(
        self, db_session, user_factory
    ) -> None:
        """בחירת 'דתי אלגנט' עוברת לתפריט"""
        user = await user_factory(
            phone_number="+972502225115",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            context={"reg_name": "ישראל", "reg_age": "35", "reg_vehicle": "טויוטה"},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "דתי אלגנט", None
        )

        assert new_state == DriverState.MENU.value

    @pytest.mark.asyncio
    async def test_cancel_goes_to_initial(self, db_session, user_factory) -> None:
        """ביטול חוזר למצב ראשוני"""
        user = await user_factory(
            phone_number="+972502225116",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "❌ ביטול", None
        )

        assert "בוטל" in response.text
        assert new_state == DriverState.INITIAL.value

    @pytest.mark.asyncio
    async def test_invalid_choice_stays(self, db_session, user_factory) -> None:
        """בחירה לא תקינה נשארת באותו מצב עם כפתורים"""
        user = await user_factory(
            phone_number="+972502225117",
            name="נהג",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "לא קיים", None
        )

        assert "❌" in response.text
        assert response.keyboard is not None
        assert new_state == DriverState.REGISTER_COLLECT_DRESS_CODE.value


# ============================================================================
# בדיקות זרימה מלאה (end-to-end)
# ============================================================================


class TestDriverRegistrationFullFlow:
    """בדיקות זרימה מלאה — כל 4 השלבים"""

    @pytest.mark.asyncio
    async def test_full_secular_registration(self, db_session, user_factory) -> None:
        """זרימה מלאה — רישום חילוני (ללא אימות)"""
        user = await user_factory(
            phone_number="+972502226111",
            name="נהג חדש",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.INITIAL.value, context={}
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        # שלב 0 — initial → collect_name
        response, state = await handler.handle_message(user, "/start", None)
        assert state == DriverState.REGISTER_COLLECT_NAME.value

        # שלב 1 — שם
        response, state = await handler.handle_message(user, "משה כהן", None)
        assert state == DriverState.REGISTER_COLLECT_BIRTH_DATE.value

        # שלב 2 — תאריך לידה
        response, state = await handler.handle_message(user, "01/06/1985", None)
        assert state == DriverState.REGISTER_COLLECT_VEHICLE.value

        # שלב 3 — רכב
        response, state = await handler.handle_message(
            user, "טויוטה קאמרי 2023", None
        )
        assert state == DriverState.REGISTER_COLLECT_DRESS_CODE.value
        assert response.keyboard is not None

        # שלב 4 — קוד לבוש חילוני → תפריט
        response, state = await handler.handle_message(user, "חילוני", None)
        assert state == DriverState.MENU.value
        assert "הרישום הושלם" in response.text

        # אימות DB
        await db_session.refresh(user)
        assert user.full_name == "משה כהן"

        from sqlalchemy import select

        result = await db_session.execute(
            select(DriverProfile).where(DriverProfile.user_id == user.id)
        )
        profile = result.scalar_one()
        assert profile.birth_date == date(1985, 6, 1)
        assert profile.vehicle_description == "טויוטה קאמרי 2023"
        assert profile.dress_code == "secular"
        assert profile.trial_starts_at is not None

    @pytest.mark.asyncio
    async def test_full_haredi_registration(self, db_session, user_factory) -> None:
        """זרימה מלאה — רישום חרדי (עם הפניה לאימות)"""
        user = await user_factory(
            phone_number="+972502226112",
            name="נהג חרדי",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.INITIAL.value, context={}
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        # שלב 0 — initial
        response, state = await handler.handle_message(user, "/start", None)
        assert state == DriverState.REGISTER_COLLECT_NAME.value

        # שלב 1 — שם
        response, state = await handler.handle_message(user, "יעקב לוי", None)
        assert state == DriverState.REGISTER_COLLECT_BIRTH_DATE.value

        # שלב 2 — תאריך לידה
        response, state = await handler.handle_message(user, "20/03/1988", None)
        assert state == DriverState.REGISTER_COLLECT_VEHICLE.value

        # שלב 3 — רכב
        response, state = await handler.handle_message(user, "מיניוואן קיה 2024", None)
        assert state == DriverState.REGISTER_COLLECT_DRESS_CODE.value

        # שלב 4 — קוד לבוש חרדי → אימות
        response, state = await handler.handle_message(
            user, "חרדי שחור לבן", None
        )
        assert state == DriverState.VERIFY_COLLECT_SELFIE.value
        assert "אימות" in response.text

        # אימות DB
        from sqlalchemy import select

        result = await db_session.execute(
            select(DriverProfile).where(DriverProfile.user_id == user.id)
        )
        profile = result.scalar_one()
        assert profile.dress_code == "ultra_orthodox"

    @pytest.mark.asyncio
    async def test_whatsapp_platform_flow(self, db_session, user_factory) -> None:
        """זרימה בסיסית ב-WhatsApp — הפלטפורמה לא אמורה לשנות לוגיקה"""
        user = await user_factory(
            phone_number="+972502226113",
            name="נהג וואטסאפ",
            role=UserRole.DRIVER,
            platform="whatsapp",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "whatsapp", DriverState.INITIAL.value, context={}
        )
        handler = DriverStateHandler(db_session, platform="whatsapp")

        response, state = await handler.handle_message(user, "/start", None)
        assert state == DriverState.REGISTER_COLLECT_NAME.value

        response, state = await handler.handle_message(user, "דוד מלך", None)
        assert state == DriverState.REGISTER_COLLECT_BIRTH_DATE.value


# ============================================================================
# בדיקות תיקוני באגים — מצבים עתידיים ו-context cleanup
# ============================================================================


class TestPostRegistrationStability:
    """בדיקות שמצבים אחרי רישום לא מחזירים לתחילת הרישום"""

    @pytest.mark.asyncio
    async def test_menu_state_does_not_restart_registration(
        self, db_session, user_factory
    ) -> None:
        """נהג במצב MENU שולח הודעה — לא חוזר לרישום"""
        user = await user_factory(
            phone_number="+972502227111",
            name="נהג בתפריט",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.MENU.value, context={}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "שלום", None)

        # חייב להישאר ב-MENU, לא לחזור ל-REGISTER
        assert new_state == DriverState.MENU.value
        assert "בהקמה" in response.text
        assert "שם המלא" not in response.text

    @pytest.mark.asyncio
    async def test_verify_state_does_not_restart_registration(
        self, db_session, user_factory
    ) -> None:
        """נהג במצב VERIFY_COLLECT_SELFIE שולח הודעה — לא חוזר לרישום"""
        user = await user_factory(
            phone_number="+972502227112",
            name="נהג באימות",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.VERIFY_COLLECT_SELFIE.value, context={}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "הנה תמונה", None)

        # חייב להישאר ב-VERIFY, לא לחזור ל-REGISTER
        assert new_state == DriverState.VERIFY_COLLECT_SELFIE.value
        assert "בהקמה" in response.text

    @pytest.mark.asyncio
    async def test_unknown_state_does_not_restart_registration(
        self, db_session, user_factory
    ) -> None:
        """מצב לא מוכר שומר על מצב נוכחי"""
        user = await user_factory(
            phone_number="+972502227113",
            name="נהג מוזר",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        # מצב שלא קיים ב-_get_handler
        await state_manager.force_state(
            user.id, "telegram", DriverState.SETTINGS_VIEW.value, context={}
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "הגדרות", None)

        assert new_state == DriverState.SETTINGS_VIEW.value
        assert "בהקמה" in response.text

    @pytest.mark.asyncio
    async def test_full_flow_then_menu_message_stays(
        self, db_session, user_factory
    ) -> None:
        """רישום מלא → MENU → הודעה נוספת → נשאר ב-MENU"""
        user = await user_factory(
            phone_number="+972502227114",
            name="נהג end-to-end",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id, "telegram", DriverState.INITIAL.value, context={}
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        # רישום מלא
        _, state = await handler.handle_message(user, "/start", None)
        _, state = await handler.handle_message(user, "יוסי כהן", None)
        _, state = await handler.handle_message(user, "01/01/1990", None)
        _, state = await handler.handle_message(user, "טויוטה 2020", None)
        _, state = await handler.handle_message(user, "חילוני", None)
        assert state == DriverState.MENU.value

        # הודעה נוספת — חייב להישאר ב-MENU
        response, state = await handler.handle_message(user, "מה חדש?", None)
        assert state == DriverState.MENU.value
        assert "בהקמה" in response.text


class TestCancelCleansContext:
    """בדיקת ניקוי קונטקסט בביטול"""

    @pytest.mark.asyncio
    async def test_cancel_clears_registration_context(
        self, db_session, user_factory
    ) -> None:
        """ביטול מ-dress code מנקה מפתחות רישום מהקונטקסט"""
        user = await user_factory(
            phone_number="+972502228111",
            name="נהג ביטול",
            role=UserRole.DRIVER,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.REGISTER_COLLECT_DRESS_CODE.value,
            context={
                "reg_name": "ישראל",
                "reg_age": "35",
                "reg_vehicle": "טויוטה",
                "reg_birth_date": "1990-01-01",
                "some_other_key": "keep_me",
            },
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "❌ ביטול", None)

        assert new_state == DriverState.INITIAL.value

        # וידוא שהקונטקסט נוקה ממפתחות רישום
        context = await state_manager.get_context(user.id, "telegram")
        assert "reg_name" not in context
        assert "reg_age" not in context
        assert "reg_vehicle" not in context
        assert "reg_birth_date" not in context
        # מפתחות אחרים נשמרים
        assert context.get("some_other_key") == "keep_me"
