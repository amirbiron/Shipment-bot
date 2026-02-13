"""
תרחיש 6 — דחיית KYC וחזרה: רישום → דחייה → רישום חוזר → אישור → תפיסה

מכסה:
- זרימת רישום שליח מלאה דרך webhook (שם, מסמך, סלפי, רכב, תקנון)
- אדמין דוחה עם הערה
- שליח שולח # ומתחיל מחדש
- אדמין מאשר → שליח יכול לתפוס
"""
import pytest
from sqlalchemy import select

from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.delivery import DeliveryStatus
from app.state_machine.states import CourierState
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.capture_service import CaptureService

from tests.scenarios.conftest import (
    send_tg,
    send_tg_callback,
    send_tg_photo,
)


@pytest.mark.scenario
class TestKycRejectionRecovery:
    """דחיית KYC וחזרה — רישום → דחייה → הגשה חוזרת → אישור"""

    async def _register_courier_via_webhook(self, client, chat_id: int):
        """פונקציית עזר — מעבירה שליח דרך כל שלבי הרישום"""
        # שם
        data = await send_tg(client, chat_id, "ישראל ישראלי")
        assert data.get("new_state") == CourierState.REGISTER_COLLECT_DOCUMENT.value

        # מסמך תעודה
        data = await send_tg_photo(client, chat_id, "doc_photo_123")
        assert data.get("new_state") == CourierState.REGISTER_COLLECT_SELFIE.value

        # סלפי
        data = await send_tg_photo(client, chat_id, "selfie_photo_123")
        assert data.get("new_state") == CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value

        # קטגוריית רכב — callback
        data = await send_tg_callback(client, chat_id, "רכב 4 מקומות")
        assert data.get("new_state") == CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value

        # תמונת רכב
        data = await send_tg_photo(client, chat_id, "vehicle_photo_123")
        assert data.get("new_state") == CourierState.REGISTER_TERMS.value

        # אישור תקנון
        data = await send_tg_callback(client, chat_id, "מאשר את התקנון")
        assert data.get("new_state") == CourierState.PENDING_APPROVAL.value

    @pytest.mark.asyncio
    async def test_kyc_reject_reregister_approve_capture(
        self,
        test_client,
        db_session,
        user_factory,
        wallet_factory,
        configure_admin,
    ):
        """זרימה מלאה: רישום → דחייה → הגשה חוזרת → אישור → תפיסה"""
        chat_id = 90001
        admin_chat_id = 99999

        # --- שלב 1: משתמש חדש ---
        data = await send_tg(test_client, chat_id, "שלום", name="ישראל")
        assert data.get("new_user") is True

        # --- שלב 2: הצטרפות כשליח ---
        data = await send_tg_callback(
            test_client, chat_id,
            "🚚 הצטרפות למנוי וקבלת משלוחים",
        )
        assert data.get("new_state") == CourierState.REGISTER_COLLECT_NAME.value

        # --- שלב 3: מעבר כל שלבי הרישום ---
        await self._register_courier_via_webhook(test_client, chat_id)

        # אימות: המשתמש ב-PENDING
        result = await db_session.execute(
            select(User).where(User.telegram_chat_id == str(chat_id))
        )
        user = result.scalar_one()
        assert user.role == UserRole.COURIER
        assert user.approval_status == ApprovalStatus.PENDING

        # --- שלב 4: אדמין דוחה ---
        data = await send_tg_callback(
            test_client, admin_chat_id,
            f"reject_courier_{user.id}",
            name="Admin",
        )

        # שליחת הערת דחייה
        data = await send_tg(
            test_client, admin_chat_id,
            "מסמכים לא ברורים, יש לצלם מחדש",
            name="Admin",
        )

        # אימות: סטטוס REJECTED — שליפה טרייה לפי chat_id (ללא expire_all)
        result = await db_session.execute(
            select(User).where(
                User.telegram_chat_id == str(chat_id)
            ).execution_options(populate_existing=True)
        )
        user = result.scalar_one()
        user_id = user.id  # שמירת ID מקומי — בטוח לגישה סינכרונית
        assert user.approval_status == ApprovalStatus.REJECTED

        # --- שלב 5: שליח שולח # — חזרה לסנדר ---
        data = await send_tg(test_client, chat_id, "#")

        # --- שלב 6: הצטרפות מחדש ---
        data = await send_tg_callback(
            test_client, chat_id,
            "🚚 הצטרפות למנוי וקבלת משלוחים",
        )
        assert data.get("new_state") == CourierState.REGISTER_COLLECT_NAME.value

        # מעבר שוב דרך כל השלבים
        await self._register_courier_via_webhook(test_client, chat_id)

        # --- שלב 7: אדמין מאשר ---
        result = await db_session.execute(
            select(User).where(
                User.telegram_chat_id == str(chat_id)
            ).execution_options(populate_existing=True)
        )
        user = result.scalar_one()
        user_id = user.id

        data = await send_tg_callback(
            test_client, admin_chat_id,
            f"approve_courier_{user_id}",
            name="Admin",
        )

        # אימות: סטטוס APPROVED
        result = await db_session.execute(
            select(User).where(
                User.id == user_id
            ).execution_options(populate_existing=True)
        )
        user = result.scalar_one()
        assert user.approval_status == ApprovalStatus.APPROVED

        # --- שלב 8: תפיסת משלוח ---
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="11111",
        )

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
        )

        capture_service = CaptureService(db_session)
        success, msg, _ = await capture_service.capture_delivery_by_token(
            delivery.token, user.id
        )
        assert success is True, f"תפיסה נכשלה אחרי אישור: {msg}"
