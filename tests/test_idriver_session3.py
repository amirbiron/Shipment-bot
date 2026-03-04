"""
בדיקות יחידה — iDriver סשן 3: אימות חרדי + אישור/דחיית מנהל

בודק:
- DriverVerificationService (submit_selfie, submit_id_document, approve, reject)
- DriverStateHandler (זרימת אימות: סלפי → ת.ז. → המתנה)
- מעברי סטטוס (UNVERIFIED → PENDING → APPROVED / REJECTED)
- הגשה מחדש אחרי דחייה
"""
import pytest
from datetime import date, datetime
from unittest.mock import patch, AsyncMock

from sqlalchemy import select

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    DriverVerificationStatus,
)
from app.state_machine.states import DriverState
from app.state_machine.manager import StateManager
from app.state_machine.driver_handler import DriverStateHandler
from app.domain.services.driver_verification_service import (
    DriverVerificationService,
    DriverApprovalResult,
)
from app.core.exceptions import ValidationException, NotFoundException


# ============================================================================
# עזר — יצירת פרופיל נהג עם סטטוס אימות
# ============================================================================


async def _create_driver_with_profile(
    db_session,
    user_factory,
    phone: str,
    dress_code: str = DressCode.HASSIDIC.value,
    verification_status: str = DriverVerificationStatus.UNVERIFIED.value,
    selfie_file_id: str | None = None,
    id_file_id: str | None = None,
    rejection_reason: str | None = None,
) -> tuple[User, DriverProfile]:
    """יוצר נהג עם פרופיל מלא לבדיקות"""
    user = await user_factory(
        phone_number=phone,
        name="נהג בדיקה",
        full_name="ישראל ישראלי",
        role=UserRole.DRIVER,
        telegram_chat_id=phone.replace("+972", ""),
    )
    profile = DriverProfile(
        user_id=user.id,
        birth_date=date(1990, 1, 1),
        vehicle_description="טויוטה 2024",
        vehicle_category="car",
        dress_code=dress_code,
        verification_status=verification_status,
        verification_selfie_file_id=selfie_file_id,
        verification_id_file_id=id_file_id,
        rejection_reason=rejection_reason,
        trial_starts_at=datetime.utcnow(),
        trial_expires_at=datetime.utcnow(),
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return user, profile


# ============================================================================
# בדיקות DriverVerificationService — submit_selfie
# ============================================================================


class TestSubmitSelfie:
    """בדיקות שמירת סלפי"""

    @pytest.mark.asyncio
    async def test_submit_selfie_saves_file_id(self, db_session, user_factory) -> None:
        """שמירת סלפי תקין"""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503001001"
        )
        service = DriverVerificationService(db_session)
        await service.submit_selfie(user.id, "selfie_file_123")

        await db_session.refresh(profile)
        assert profile.verification_selfie_file_id == "selfie_file_123"

    @pytest.mark.asyncio
    async def test_submit_selfie_empty_raises(self, db_session, user_factory) -> None:
        """מזהה קובץ ריק זורק שגיאה"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503001002"
        )
        service = DriverVerificationService(db_session)
        with pytest.raises(ValidationException):
            await service.submit_selfie(user.id, "")

    @pytest.mark.asyncio
    async def test_submit_selfie_whitespace_raises(self, db_session, user_factory) -> None:
        """מזהה קובץ עם רווחים בלבד זורק שגיאה"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503001003"
        )
        service = DriverVerificationService(db_session)
        with pytest.raises(ValidationException):
            await service.submit_selfie(user.id, "   ")

    @pytest.mark.asyncio
    async def test_submit_selfie_no_profile_raises(self, db_session, user_factory) -> None:
        """משתמש ללא פרופיל זורק שגיאה"""
        user = await user_factory(
            phone_number="+972503001004",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverVerificationService(db_session)
        with pytest.raises(NotFoundException):
            await service.submit_selfie(user.id, "selfie_file_123")


# ============================================================================
# בדיקות DriverVerificationService — submit_id_document
# ============================================================================


class TestSubmitIdDocument:
    """בדיקות שמירת תעודת זהות"""

    @pytest.mark.asyncio
    async def test_submit_id_saves_and_sets_pending(
        self, db_session, user_factory
    ) -> None:
        """שמירת ת.ז. מעדכנת סטטוס ל-PENDING"""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503002001",
            selfie_file_id="selfie_ok",
        )
        service = DriverVerificationService(db_session)
        await service.submit_id_document(user.id, "id_file_456")

        await db_session.refresh(profile)
        assert profile.verification_id_file_id == "id_file_456"
        assert profile.verification_status == DriverVerificationStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_submit_id_empty_raises(self, db_session, user_factory) -> None:
        """מזהה קובץ ריק זורק שגיאה"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503002002"
        )
        service = DriverVerificationService(db_session)
        with pytest.raises(ValidationException):
            await service.submit_id_document(user.id, "")

    @pytest.mark.asyncio
    async def test_submit_id_no_profile_raises(self, db_session, user_factory) -> None:
        """משתמש ללא פרופיל זורק שגיאה"""
        user = await user_factory(
            phone_number="+972503002003",
            name="נהג",
            role=UserRole.DRIVER,
        )
        service = DriverVerificationService(db_session)
        with pytest.raises(NotFoundException):
            await service.submit_id_document(user.id, "id_file_456")


# ============================================================================
# בדיקות DriverVerificationService — approve_driver
# ============================================================================


class TestApproveDriver:
    """בדיקות אישור נהג"""

    @pytest.mark.asyncio
    async def test_approve_pending_driver(self, db_session, user_factory) -> None:
        """אישור נהג PENDING מצליח"""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503003001",
            verification_status=DriverVerificationStatus.PENDING.value,
            selfie_file_id="selfie_ok",
            id_file_id="id_ok",
        )
        result = await DriverVerificationService.approve_driver(db_session, user.id)

        assert result.success is True
        assert "אושר" in result.message
        assert result.user is not None
        assert result.profile is not None

        await db_session.refresh(profile)
        assert profile.verification_status == DriverVerificationStatus.APPROVED.value
        assert profile.rejection_reason is None

    @pytest.mark.asyncio
    async def test_approve_already_approved(self, db_session, user_factory) -> None:
        """אישור נהג שכבר מאושר — נכשל"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503003002",
            verification_status=DriverVerificationStatus.APPROVED.value,
        )
        result = await DriverVerificationService.approve_driver(db_session, user.id)

        assert result.success is False
        assert "כבר מאושר" in result.message

    @pytest.mark.asyncio
    async def test_approve_unverified_fails(self, db_session, user_factory) -> None:
        """אישור נהג UNVERIFIED — נכשל (לא ממתין לאישור)"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503003003",
            verification_status=DriverVerificationStatus.UNVERIFIED.value,
        )
        result = await DriverVerificationService.approve_driver(db_session, user.id)

        assert result.success is False
        assert "אינו ממתין" in result.message

    @pytest.mark.asyncio
    async def test_approve_nonexistent_user(self, db_session) -> None:
        """אישור משתמש לא קיים"""
        result = await DriverVerificationService.approve_driver(db_session, 999999)
        assert result.success is False
        assert "לא נמצא" in result.message

    @pytest.mark.asyncio
    async def test_approve_user_without_profile(self, db_session, user_factory) -> None:
        """אישור משתמש ללא פרופיל נהג"""
        user = await user_factory(
            phone_number="+972503003004",
            name="נהג",
            role=UserRole.DRIVER,
        )
        result = await DriverVerificationService.approve_driver(db_session, user.id)
        assert result.success is False
        assert "לא נמצא פרופיל" in result.message


# ============================================================================
# בדיקות DriverVerificationService — reject_driver
# ============================================================================


class TestRejectDriver:
    """בדיקות דחיית נהג"""

    @pytest.mark.asyncio
    async def test_reject_pending_driver(self, db_session, user_factory) -> None:
        """דחיית נהג PENDING מצליחה"""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503004001",
            verification_status=DriverVerificationStatus.PENDING.value,
            selfie_file_id="selfie_ok",
            id_file_id="id_ok",
        )
        result = await DriverVerificationService.reject_driver(
            db_session, user.id, rejection_reason="תמונה לא ברורה"
        )

        assert result.success is True
        assert "נדחה" in result.message

        await db_session.refresh(profile)
        assert profile.verification_status == DriverVerificationStatus.REJECTED.value
        assert profile.rejection_reason == "תמונה לא ברורה"
        # קבצי אימות מנוקים לאפשר הגשה מחדש
        assert profile.verification_selfie_file_id is None
        assert profile.verification_id_file_id is None

    @pytest.mark.asyncio
    async def test_reject_without_reason(self, db_session, user_factory) -> None:
        """דחייה ללא סיבה — מצליחה"""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503004002",
            verification_status=DriverVerificationStatus.PENDING.value,
            selfie_file_id="selfie_ok",
            id_file_id="id_ok",
        )
        result = await DriverVerificationService.reject_driver(db_session, user.id)

        assert result.success is True
        await db_session.refresh(profile)
        assert profile.rejection_reason is None

    @pytest.mark.asyncio
    async def test_reject_empty_reason_normalized_to_none(
        self, db_session, user_factory
    ) -> None:
        """סיבת דחייה ריקה/רווחים מנורמלת ל-None"""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503004003",
            verification_status=DriverVerificationStatus.PENDING.value,
            selfie_file_id="selfie_ok",
            id_file_id="id_ok",
        )
        result = await DriverVerificationService.reject_driver(
            db_session, user.id, rejection_reason="   "
        )

        assert result.success is True
        await db_session.refresh(profile)
        assert profile.rejection_reason is None

    @pytest.mark.asyncio
    async def test_reject_already_approved_fails(
        self, db_session, user_factory
    ) -> None:
        """דחיית נהג מאושר — נכשלת"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503004004",
            verification_status=DriverVerificationStatus.APPROVED.value,
        )
        result = await DriverVerificationService.reject_driver(db_session, user.id)
        assert result.success is False
        assert "לא ניתן לדחות" in result.message

    @pytest.mark.asyncio
    async def test_reject_already_rejected_fails(
        self, db_session, user_factory
    ) -> None:
        """דחיית נהג שכבר נדחה — נכשלת"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503004005",
            verification_status=DriverVerificationStatus.REJECTED.value,
        )
        result = await DriverVerificationService.reject_driver(db_session, user.id)
        assert result.success is False
        assert "כבר נדחה" in result.message

    @pytest.mark.asyncio
    async def test_reject_nonexistent_user(self, db_session) -> None:
        """דחיית משתמש לא קיים"""
        result = await DriverVerificationService.reject_driver(db_session, 999999)
        assert result.success is False
        assert "לא נמצא" in result.message


# ============================================================================
# בדיקות DriverStateHandler — זרימת אימות (VERIFY_COLLECT_SELFIE)
# ============================================================================


class TestVerifySelfieHandler:
    """בדיקות handler סלפי"""

    @pytest.mark.asyncio
    async def test_selfie_state_shows_instructions_without_photo(
        self, db_session, user_factory
    ) -> None:
        """שליחת טקסט ללא תמונה — הצגת הנחיות"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503005001"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_COLLECT_SELFIE.value,
            context={"reg_name": "ישראל", "reg_age": "35", "reg_vehicle": "טויוטה"},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "הנה סלפי", None)

        assert new_state == DriverState.VERIFY_COLLECT_SELFIE.value
        assert "סלפי" in response.text
        assert "שלב אימות 1" in response.text

    @pytest.mark.asyncio
    async def test_selfie_with_photo_proceeds(
        self, db_session, user_factory
    ) -> None:
        """שליחת תמונה — שומר סלפי ועובר לשלב ת.ז."""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503005002"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_COLLECT_SELFIE.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        # סימולציה של שליחת תמונה דרך photo_file_id
        response, new_state = await handler.handle_message(
            user, "", "selfie_photo_abc"
        )

        assert new_state == DriverState.VERIFY_COLLECT_ID_DOCUMENT.value
        assert "סלפי התקבל" in response.text

        await db_session.refresh(profile)
        assert profile.verification_selfie_file_id == "selfie_photo_abc"

    @pytest.mark.asyncio
    async def test_selfie_cancel_goes_to_menu(
        self, db_session, user_factory
    ) -> None:
        """ביטול מחזיר לתפריט"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503005003"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_COLLECT_SELFIE.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "❌ ביטול", None
        )

        assert new_state == DriverState.MENU.value
        assert "בוטל" in response.text

    @pytest.mark.asyncio
    async def test_selfie_shows_rejection_reason(
        self, db_session, user_factory
    ) -> None:
        """נהג שנדחה רואה את סיבת הדחייה בכניסה מחדש"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503005004",
            verification_status=DriverVerificationStatus.REJECTED.value,
            rejection_reason="תמונה מטושטשת",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_COLLECT_SELFIE.value,
            context={"reg_name": "ישראל", "reg_age": "35", "reg_vehicle": "טויוטה"},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "שלום", None)

        assert "נדחתה" in response.text
        assert "תמונה מטושטשת" in response.text


# ============================================================================
# בדיקות DriverStateHandler — זרימת אימות (VERIFY_COLLECT_ID_DOCUMENT)
# ============================================================================


class TestVerifyIdDocumentHandler:
    """בדיקות handler תעודת זהות"""

    @pytest.mark.asyncio
    async def test_id_state_shows_instructions_without_photo(
        self, db_session, user_factory
    ) -> None:
        """שליחת טקסט ללא תמונה — הצגת הנחיות"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503006001",
            selfie_file_id="selfie_ok",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_COLLECT_ID_DOCUMENT.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "הנה תעודה", None)

        assert new_state == DriverState.VERIFY_COLLECT_ID_DOCUMENT.value
        assert "תעודת הזהות" in response.text

    @pytest.mark.asyncio
    @patch(
        "app.state_machine.driver_handler.DriverStateHandler._notify_admin_driver_verification",
        new_callable=AsyncMock,
    )
    async def test_id_with_photo_proceeds_to_pending(
        self, mock_notify, db_session, user_factory
    ) -> None:
        """שליחת תמונת ת.ז. — שומר ועובר ל-PENDING"""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503006002",
            selfie_file_id="selfie_ok",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_COLLECT_ID_DOCUMENT.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "", "id_photo_xyz"
        )

        assert new_state == DriverState.VERIFY_PENDING_APPROVAL.value
        assert "התקבלה" in response.text

        await db_session.refresh(profile)
        assert profile.verification_id_file_id == "id_photo_xyz"
        assert profile.verification_status == DriverVerificationStatus.PENDING.value

        # ודא שהתראה נשלחה למנהל
        mock_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_id_cancel_goes_to_menu(
        self, db_session, user_factory
    ) -> None:
        """ביטול מחזיר לתפריט"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503006003"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_COLLECT_ID_DOCUMENT.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(
            user, "❌ ביטול", None
        )

        assert new_state == DriverState.MENU.value
        assert "בוטל" in response.text


# ============================================================================
# בדיקות DriverStateHandler — VERIFY_PENDING_APPROVAL
# ============================================================================


class TestVerifyPendingApprovalHandler:
    """בדיקות handler המתנה לאישור"""

    @pytest.mark.asyncio
    async def test_pending_shows_status(self, db_session, user_factory) -> None:
        """הודעה בזמן המתנה — מציגה סטטוס"""
        user, _ = await _create_driver_with_profile(
            db_session, user_factory, "+972503007001",
            verification_status=DriverVerificationStatus.PENDING.value,
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_PENDING_APPROVAL.value,
            context={},
        )

        handler = DriverStateHandler(db_session, platform="telegram")
        response, new_state = await handler.handle_message(user, "מה המצב?", None)

        assert new_state == DriverState.VERIFY_PENDING_APPROVAL.value
        assert "בבדיקה" in response.text


# ============================================================================
# בדיקות זרימה מלאה — סלפי → ת.ז. → המתנה
# ============================================================================


class TestVerificationFullFlow:
    """בדיקות זרימה מלאה של אימות"""

    @pytest.mark.asyncio
    @patch(
        "app.state_machine.driver_handler.DriverStateHandler._notify_admin_driver_verification",
        new_callable=AsyncMock,
    )
    async def test_full_verification_flow(
        self, mock_notify, db_session, user_factory
    ) -> None:
        """זרימה מלאה: סלפי → ת.ז. → המתנה"""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503008001"
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_COLLECT_SELFIE.value,
            context={"reg_name": "ישראל", "reg_age": "35", "reg_vehicle": "טויוטה"},
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        # שלב 1 — כניסה ראשונה (הנחיות)
        response, state = await handler.handle_message(user, "שלום", None)
        assert state == DriverState.VERIFY_COLLECT_SELFIE.value
        assert "סלפי" in response.text

        # שלב 2 — שליחת סלפי
        response, state = await handler.handle_message(
            user, "", "selfie_full_flow"
        )
        assert state == DriverState.VERIFY_COLLECT_ID_DOCUMENT.value

        # שלב 3 — שליחת ת.ז.
        response, state = await handler.handle_message(
            user, "", "id_full_flow"
        )
        assert state == DriverState.VERIFY_PENDING_APPROVAL.value
        assert "התקבלה" in response.text

        # וידוא DB
        await db_session.refresh(profile)
        assert profile.verification_selfie_file_id == "selfie_full_flow"
        assert profile.verification_id_file_id == "id_full_flow"
        assert profile.verification_status == DriverVerificationStatus.PENDING.value

        # שלב 4 — הודעה בזמן המתנה
        response, state = await handler.handle_message(user, "מתי?", None)
        assert state == DriverState.VERIFY_PENDING_APPROVAL.value
        assert "בבדיקה" in response.text


# ============================================================================
# בדיקות הגשה מחדש אחרי דחייה
# ============================================================================


class TestRejectionResubmission:
    """בדיקות הגשה מחדש אחרי דחייה"""

    @pytest.mark.asyncio
    @patch(
        "app.state_machine.driver_handler.DriverStateHandler._notify_admin_driver_verification",
        new_callable=AsyncMock,
    )
    async def test_rejected_driver_can_resubmit(
        self, mock_notify, db_session, user_factory
    ) -> None:
        """נהג שנדחה יכול להגיש מחדש סלפי + ת.ז."""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503009001",
            verification_status=DriverVerificationStatus.REJECTED.value,
            rejection_reason="תמונה מטושטשת",
        )
        state_manager = StateManager(db_session)
        await state_manager.force_state(
            user.id,
            "telegram",
            DriverState.VERIFY_COLLECT_SELFIE.value,
            context={"reg_name": "ישראל", "reg_age": "35", "reg_vehicle": "טויוטה"},
        )
        handler = DriverStateHandler(db_session, platform="telegram")

        # כניסה — מציג סיבת דחייה
        response, state = await handler.handle_message(user, "חזרתי", None)
        assert "נדחתה" in response.text
        assert "תמונה מטושטשת" in response.text
        assert state == DriverState.VERIFY_COLLECT_SELFIE.value

        # שליחת סלפי חדש
        response, state = await handler.handle_message(
            user, "", "new_selfie"
        )
        assert state == DriverState.VERIFY_COLLECT_ID_DOCUMENT.value

        # שליחת ת.ז. חדשה
        response, state = await handler.handle_message(
            user, "", "new_id"
        )
        assert state == DriverState.VERIFY_PENDING_APPROVAL.value

        # וידוא DB — סטטוס חזר ל-PENDING
        await db_session.refresh(profile)
        assert profile.verification_status == DriverVerificationStatus.PENDING.value
        assert profile.verification_selfie_file_id == "new_selfie"
        assert profile.verification_id_file_id == "new_id"

    @pytest.mark.asyncio
    async def test_reject_then_approve_on_resubmission(
        self, db_session, user_factory
    ) -> None:
        """דחייה → הגשה מחדש (PENDING) → אישור"""
        user, profile = await _create_driver_with_profile(
            db_session, user_factory, "+972503009002",
            verification_status=DriverVerificationStatus.PENDING.value,
            selfie_file_id="selfie_v2",
            id_file_id="id_v2",
        )

        # דחייה
        result = await DriverVerificationService.reject_driver(
            db_session, user.id, rejection_reason="לא ברור"
        )
        assert result.success is True

        await db_session.refresh(profile)
        assert profile.verification_status == DriverVerificationStatus.REJECTED.value

        # סימולציה — נהג מגיש מחדש (DB עדכון ישיר)
        profile.verification_selfie_file_id = "selfie_v3"
        profile.verification_id_file_id = "id_v3"
        profile.verification_status = DriverVerificationStatus.PENDING.value
        await db_session.commit()

        # אישור
        result = await DriverVerificationService.approve_driver(db_session, user.id)
        assert result.success is True

        await db_session.refresh(profile)
        assert profile.verification_status == DriverVerificationStatus.APPROVED.value


# ============================================================================
# בדיקות _pop_pending_rejection — תמיכה בפורמט חדש (type:id)
# ============================================================================


class TestPendingRejectionRedis:
    """בדיקות מנגנון Redis לדחייה ממתינה"""

    @pytest.mark.asyncio
    async def test_set_and_pop_courier(self, fake_redis) -> None:
        """שמירה ושליפת דחיית שליח"""
        from app.api.webhooks.telegram import (
            _set_pending_rejection,
            _pop_pending_rejection,
        )

        saved = await _set_pending_rejection("admin_123", 42, target_type="courier")
        assert saved is True

        result = await _pop_pending_rejection("admin_123")
        assert result == ("courier", 42)

    @pytest.mark.asyncio
    async def test_set_and_pop_driver(self, fake_redis) -> None:
        """שמירה ושליפת דחיית נהג"""
        from app.api.webhooks.telegram import (
            _set_pending_rejection,
            _pop_pending_rejection,
        )

        saved = await _set_pending_rejection("admin_456", 99, target_type="driver")
        assert saved is True

        result = await _pop_pending_rejection("admin_456")
        assert result == ("driver", 99)

    @pytest.mark.asyncio
    async def test_pop_empty_returns_none(self, fake_redis) -> None:
        """שליפה מרשומה ריקה — None"""
        from app.api.webhooks.telegram import _pop_pending_rejection

        result = await _pop_pending_rejection("no_such_admin")
        assert result is None

    @pytest.mark.asyncio
    async def test_backward_compat_plain_number(self, fake_redis) -> None:
        """תאימות לאחור — מספר ללא prefix מפורש כ-courier"""
        from app.api.webhooks.telegram import _pop_pending_rejection

        # סימולציה של ערך ישן (מספר בלבד)
        fake_redis._store["shipmentbot:tg:pending_rejection:admin_old"] = "77"

        result = await _pop_pending_rejection("admin_old")
        assert result == ("courier", 77)

    @pytest.mark.asyncio
    async def test_clear_pending_rejection(self, fake_redis) -> None:
        """מחיקת דחייה ממתינה"""
        from app.api.webhooks.telegram import (
            _set_pending_rejection,
            _clear_pending_rejection,
            _pop_pending_rejection,
        )

        await _set_pending_rejection("admin_789", 50, target_type="driver")
        await _clear_pending_rejection("admin_789")

        result = await _pop_pending_rejection("admin_789")
        assert result is None
