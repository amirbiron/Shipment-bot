"""
תרחיש 7 — הפקדה ויתרה: ארנק, אשראי, ותפיסה בגבולות מגבלה

מכסה:
- תפיסה עם אשראי שלילי (בגבול credit_limit)
- כשלון תפיסה כשחורגים מ-credit_limit
- זרימת הפקדה דרך webhook (ארנק → בקשה → העלאת צילום מסך)
"""
import pytest
from sqlalchemy import select

from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.delivery import DeliveryStatus
from app.db.models.courier_wallet import CourierWallet
from app.state_machine.states import CourierState
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.capture_service import CaptureService

from tests.scenarios.conftest import (
    assert_delivery_status,
    assert_wallet_balance,
    assert_ledger_count,
    send_tg,
    send_tg_callback,
    send_tg_photo,
)


@pytest.mark.scenario
class TestDepositAndCredit:
    """הפקדה ויתרה — תפיסה באשראי ותהליך הפקדה"""

    @pytest.mark.asyncio
    async def test_capture_within_credit_limit(
        self, db_session, user_factory, wallet_factory
    ):
        """תפיסה עם אשראי — balance שלילי בגבול credit_limit"""
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="11111",
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(
            courier_id=courier.id, balance=0.0, credit_limit=-100.0
        )

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
        )

        # תפיסה — balance ירד ל--10, בגבול credit_limit של -100
        capture_service = CaptureService(db_session)
        success, msg, _ = await capture_service.capture_delivery_by_token(
            delivery.token, courier.id
        )
        assert success is True, f"תפיסה נכשלה: {msg}"

        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.CAPTURED)
        await assert_wallet_balance(db_session, courier.id, -10.0)
        await assert_ledger_count(db_session, courier.id, 1)

        # סימון כנמסר
        result = await delivery_service.mark_delivered(delivery.id)
        assert result is not None
        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.DELIVERED)

    @pytest.mark.asyncio
    async def test_capture_exceeds_credit_limit_fails(
        self, db_session, user_factory, wallet_factory
    ):
        """חריגה מ-credit_limit — תפיסה נכשלת"""
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="11111",
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        # balance=-90, credit_limit=-100 → יתרה עתידית -110 < -100
        await wallet_factory(
            courier_id=courier.id, balance=-90.0, credit_limit=-100.0
        )

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=20.0,
        )

        capture_service = CaptureService(db_session)
        success, msg, _ = await capture_service.capture_delivery_by_token(
            delivery.token, courier.id
        )
        assert success is False
        assert "יתרה" in msg

        # אימות: משלוח ועארנק לא השתנו
        await assert_delivery_status(db_session, delivery.id, DeliveryStatus.OPEN)
        await assert_wallet_balance(db_session, courier.id, -90.0)
        await assert_ledger_count(db_session, courier.id, 0)

    @pytest.mark.asyncio
    async def test_deposit_flow_via_webhook(
        self, test_client, db_session, user_factory, wallet_factory
    ):
        """זרימת הפקדה דרך webhook — שליח מבקש טעינה ומעלה צילום מסך"""
        # יצירת שליח ישירות דרך factory (ללא webhook כדי למנוע כפילויות)
        chat_id = 80001
        courier = await user_factory(
            phone_number=f"tg:{chat_id}",
            name="שליח בדיקה",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
            telegram_chat_id=str(chat_id),
            platform="telegram",
        )
        await wallet_factory(courier_id=courier.id, balance=-50.0, credit_limit=-100.0)

        # הגדרת state ל-COURIER_MENU
        from app.state_machine.manager import StateManager
        sm = StateManager(db_session)
        await sm.force_state(
            courier.id, "telegram",
            CourierState.MENU.value,
            context={},
        )

        # שלב 1: שליח שולח "💳 מנוי" מתפריט — מצפים לתפריט חבילות (DEPOSIT_REQUEST)
        data = await send_tg(test_client, chat_id, "💳 מנוי")
        new_state = data.get("new_state", "")
        assert new_state == CourierState.DEPOSIT_REQUEST.value, (
            f"צפי: {CourierState.DEPOSIT_REQUEST.value}, בפועל: {new_state}"
        )

        # שלב 2: בחירת חבילה — מצפים למעבר ל-DEPOSIT_UPLOAD עם הנחיות PayBox
        data = await send_tg(test_client, chat_id, "📦 חודש אחד")
        new_state = data.get("new_state", "")
        assert new_state == CourierState.DEPOSIT_UPLOAD.value, (
            f"צפי: {CourierState.DEPOSIT_UPLOAD.value}, בפועל: {new_state}"
        )

        # שלב 3: שליח מעלה צילום מסך תשלום — מצפים לחזרה לתפריט
        data = await send_tg_photo(test_client, chat_id, "payment_screenshot")
        new_state = data.get("new_state", "")
        assert new_state == CourierState.MENU.value, (
            f"צפי: {CourierState.MENU.value}, בפועל: {new_state}"
        )

    @pytest.mark.asyncio
    async def test_multiple_captures_accumulate_debt(
        self, db_session, user_factory, wallet_factory
    ):
        """תפיסות מרובות צוברות חוב בארנק"""
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            telegram_chat_id="11111",
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
        )
        await wallet_factory(
            courier_id=courier.id, balance=0.0, credit_limit=-100.0
        )

        delivery_service = DeliveryService(db_session)
        capture_service = CaptureService(db_session)

        # תפיסה ראשונה — fee=10 → balance=-10
        d1 = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="רחוב 1, עיר א",
            dropoff_address="רחוב 2, עיר ב",
            fee=10.0,
        )
        ok1, _, _ = await capture_service.capture_delivery_by_token(
            d1.token, courier.id
        )
        assert ok1 is True
        await assert_wallet_balance(db_session, courier.id, -10.0)

        # תפיסה שנייה — fee=30 → balance=-40
        d2 = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="רחוב 3, עיר ג",
            dropoff_address="רחוב 4, עיר ד",
            fee=30.0,
        )
        ok2, _, _ = await capture_service.capture_delivery_by_token(
            d2.token, courier.id
        )
        assert ok2 is True
        await assert_wallet_balance(db_session, courier.id, -40.0)

        # תפיסה שלישית — fee=70 → balance=-110 > credit_limit=-100 → נכשל
        d3 = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="רחוב 5, עיר ה",
            dropoff_address="רחוב 6, עיר ו",
            fee=70.0,
        )
        ok3, msg3, _ = await capture_service.capture_delivery_by_token(
            d3.token, courier.id
        )
        assert ok3 is False
        assert "יתרה" in msg3

        # אימות: ארנק נשאר על -40, 2 רשומות ledger
        await assert_wallet_balance(db_session, courier.id, -40.0)
        await assert_ledger_count(db_session, courier.id, 2)
