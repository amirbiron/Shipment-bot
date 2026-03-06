"""
בדיקות יחידה ל-CourierApprovalService — לוגיקת אישור ודחיית שליחים
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.db.models.user import User, UserRole, ApprovalStatus
from app.domain.services.courier_approval_service import (
    CourierApprovalService,
    ApprovalResult,
)


@pytest.mark.unit
class TestApprove:
    """בדיקות אישור שליח"""

    async def test_approve_pending_courier(self, db_session, user_factory):
        """אישור שליח בסטטוס PENDING — צריך להצליח"""
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח לבדיקה",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.PENDING,
        )

        result = await CourierApprovalService.approve(db_session, courier.id)

        assert result.success is True
        assert result.user is not None
        assert result.user.approval_status == ApprovalStatus.APPROVED
        assert "אושר" in result.message

    async def test_approve_already_approved(self, db_session, user_factory):
        """אישור שליח שכבר מאושר — צריך להיכשל עם הודעה מתאימה"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        result = await CourierApprovalService.approve(db_session, courier.id)

        assert result.success is False
        assert "כבר מאושר" in result.message

    async def test_approve_blocked_courier(self, db_session, user_factory):
        """אישור שליח חסום — צריך להיכשל"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.BLOCKED,
        )

        result = await CourierApprovalService.approve(db_session, courier.id)

        assert result.success is False
        assert "חסום" in result.message

    async def test_approve_nonexistent_user(self, db_session):
        """אישור משתמש שלא קיים"""
        result = await CourierApprovalService.approve(db_session, 99999)

        assert result.success is False
        assert "לא נמצא" in result.message

    async def test_approve_non_courier(self, db_session, user_factory):
        """אישור משתמש שאינו שליח (ו-approval_status לא PENDING) — צריך להיכשל"""
        sender = await user_factory(
            phone_number="+972501111111",
            role=UserRole.SENDER,
            approval_status=ApprovalStatus.APPROVED,
        )

        result = await CourierApprovalService.approve(db_session, sender.id)

        assert result.success is False
        assert "אינו נהג" in result.message

    async def test_approve_sender_with_pending_status(self, db_session, user_factory):
        """שליח שלחץ # וחזר ל-SENDER עם PENDING — עדיין ניתן לאישור"""
        user = await user_factory(
            phone_number="+972502222222",
            role=UserRole.SENDER,
            approval_status=ApprovalStatus.PENDING,
        )

        result = await CourierApprovalService.approve(db_session, user.id)

        assert result.success is True
        assert result.user.role == UserRole.COURIER
        assert result.user.approval_status == ApprovalStatus.APPROVED


@pytest.mark.unit
class TestReject:
    """בדיקות דחיית שליח"""

    async def test_reject_pending_courier(self, db_session, user_factory):
        """דחיית שליח בסטטוס PENDING — צריך להצליח"""
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח לבדיקה",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.PENDING,
        )

        result = await CourierApprovalService.reject(db_session, courier.id)

        assert result.success is True
        assert result.user.approval_status == ApprovalStatus.REJECTED
        assert "נדחה" in result.message

    async def test_reject_with_note(self, db_session, user_factory):
        """דחייה עם הערה — צריכה להישמר"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.PENDING,
        )

        result = await CourierApprovalService.reject(
            db_session, courier.id, rejection_note="תמונה לא ברורה"
        )

        assert result.success is True
        assert result.user.rejection_note == "תמונה לא ברורה"

    async def test_reject_normalizes_empty_note(self, db_session, user_factory):
        """הערה ריקה צריכה להיות None"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.PENDING,
        )

        result = await CourierApprovalService.reject(
            db_session, courier.id, rejection_note="   "
        )

        assert result.success is True
        assert result.user.rejection_note is None

    async def test_reject_already_approved(self, db_session, user_factory):
        """דחיית שליח מאושר — צריכה להיכשל"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )

        result = await CourierApprovalService.reject(db_session, courier.id)

        assert result.success is False
        assert "כבר מאושר" in result.message

    async def test_reject_already_rejected(self, db_session, user_factory):
        """דחיית שליח שכבר נדחה — צריכה להיכשל"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.REJECTED,
        )

        result = await CourierApprovalService.reject(db_session, courier.id)

        assert result.success is False
        assert "כבר נדחה" in result.message

    async def test_reject_blocked_courier(self, db_session, user_factory):
        """דחיית שליח חסום — צריכה להיכשל"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.BLOCKED,
        )

        result = await CourierApprovalService.reject(db_session, courier.id)

        assert result.success is False
        assert "חסום" in result.message

    async def test_reject_sender_with_pending_reverts_to_courier(
        self, db_session, user_factory
    ):
        """שליח שלחץ # וחזר ל-SENDER — עדיין ניתן לדחייה"""
        user = await user_factory(
            phone_number="+972502222222",
            role=UserRole.SENDER,
            approval_status=ApprovalStatus.PENDING,
        )

        result = await CourierApprovalService.reject(db_session, user.id)

        assert result.success is True
        assert result.user.role == UserRole.COURIER


@pytest.mark.unit
class TestNotifyAfterDecision:
    """בדיקות שליחת הודעות לאחר החלטה"""

    async def test_notify_approved_telegram(self, db_session, user_factory):
        """הודעת אישור לשליח בטלגרם"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            telegram_chat_id="12345",
        )

        send_tg = AsyncMock()
        send_wa = AsyncMock()

        with patch.object(
            AdminNotificationService := __import__(
                "app.domain.services.admin_notification_service",
                fromlist=["AdminNotificationService"],
            ).AdminNotificationService,
            "notify_group_courier_decision",
            new_callable=AsyncMock,
        ):
            await CourierApprovalService.notify_after_decision(
                user=courier,
                action="approve",
                admin_name="אדמין",
                send_telegram_fn=send_tg,
                send_whatsapp_fn=send_wa,
            )

        send_tg.assert_awaited_once()
        # בטלגרם — לא שולחים ב-WhatsApp
        send_wa.assert_not_awaited()

    async def test_notify_rejected_whatsapp(self, db_session, user_factory):
        """הודעת דחייה לשליח בוואטסאפ (אין telegram_chat_id)"""
        courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="whatsapp",
        )

        send_tg = AsyncMock()
        send_wa = AsyncMock()

        with patch(
            "app.domain.services.courier_approval_service.AdminNotificationService"
        ) as mock_admin:
            mock_admin.notify_group_courier_decision = AsyncMock()

            await CourierApprovalService.notify_after_decision(
                user=courier,
                action="reject",
                admin_name="אדמין",
                send_telegram_fn=send_tg,
                send_whatsapp_fn=send_wa,
            )

        send_wa.assert_awaited_once()
        send_tg.assert_not_awaited()

    async def test_notify_skips_tg_placeholder_phone(self, db_session, user_factory):
        """מספר טלפון tg: — לא צריך לשלוח בוואטסאפ"""
        courier = await user_factory(
            phone_number="tg:12345",
            role=UserRole.COURIER,
        )

        send_wa = AsyncMock()

        with patch(
            "app.domain.services.courier_approval_service.AdminNotificationService"
        ) as mock_admin:
            mock_admin.notify_group_courier_decision = AsyncMock()

            await CourierApprovalService.notify_after_decision(
                user=courier,
                action="approve",
                admin_name="אדמין",
                send_whatsapp_fn=send_wa,
            )

        send_wa.assert_not_awaited()
