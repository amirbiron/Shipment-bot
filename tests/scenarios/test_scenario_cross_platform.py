"""
תרחיש 4 — חוצה פלטפורמות: שולח בוואטסאפ, שליח בטלגרם

מכסה:
- ניתוב outbox לפלטפורמה הנכונה
- שידור broadcast לשתי פלטפורמות (משלוח ישיר)
- שידור לקבוצת תחנה (משלוח תחנה)
- הודעת תפיסה לשולח בוואטסאפ
"""
import pytest
from sqlalchemy import select

from app.db.models.user import UserRole, ApprovalStatus
from app.db.models.delivery import DeliveryStatus
from app.db.models.outbox_message import OutboxMessage, MessagePlatform
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.capture_service import CaptureService

from tests.scenarios.conftest import (
    assert_delivery_status,
    assert_outbox_count,
)


@pytest.mark.scenario
class TestCrossPlatform:
    """חוצה פלטפורמות — ניתוב הודעות נכון"""

    @pytest.mark.asyncio
    async def test_whatsapp_sender_telegram_courier_outbox_routing(
        self, db_session, user_factory, wallet_factory
    ):
        """שולח WA + שליח TG — broadcast לשניהם, capture_notification ל-WA"""
        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח WA",
            role=UserRole.SENDER,
            platform="whatsapp",
            # ללא telegram_chat_id — notification צריך ללכת לוואטסאפ
        )
        courier = await user_factory(
            phone_number="+972502222222",
            name="שליח TG",
            role=UserRole.COURIER,
            approval_status=ApprovalStatus.APPROVED,
            platform="telegram",
            telegram_chat_id="22222",
        )
        await wallet_factory(courier_id=courier.id, balance=100.0)

        # יצירת משלוח ישיר — broadcast לשתי פלטפורמות
        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
        )

        # אימות: 2 הודעות broadcast — אחת WA, אחת TG
        result = await db_session.execute(
            select(OutboxMessage).where(
                OutboxMessage.message_type == "delivery_broadcast"
            )
        )
        broadcasts = list(result.scalars().all())
        assert len(broadcasts) == 2
        platforms = {m.platform for m in broadcasts}
        assert MessagePlatform.WHATSAPP in platforms
        assert MessagePlatform.TELEGRAM in platforms

        # תפיסה
        capture_service = CaptureService(db_session)
        success, _, _ = await capture_service.capture_delivery_by_token(
            delivery.token, courier.id
        )
        assert success is True

        # אימות: הודעת capture לשולח הלכה ל-WA (כי אין לו telegram_chat_id)
        result = await db_session.execute(
            select(OutboxMessage).where(
                OutboxMessage.message_type == "capture_notification_sender"
            )
        )
        capture_msgs = list(result.scalars().all())
        assert len(capture_msgs) >= 1
        # ההודעה צריכה ללכת לוואטסאפ עם מספר הטלפון של השולח
        wa_msg = [m for m in capture_msgs if m.platform == MessagePlatform.WHATSAPP]
        assert len(wa_msg) >= 1
        assert wa_msg[0].recipient_id == sender.phone_number

    @pytest.mark.asyncio
    async def test_station_delivery_broadcasts_to_group(
        self, db_session, user_factory, station_factory
    ):
        """משלוח תחנה עם קבוצה ציבורית — broadcast לקבוצה בלבד"""
        owner = await user_factory(
            phone_number="+972500000001",
            name="בעלים",
            role=UserRole.STATION_OWNER,
        )
        station = await station_factory(
            owner_id=owner.id,
            public_group_chat_id="-100GROUP123",
        )

        sender = await user_factory(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
        )

        delivery_service = DeliveryService(db_session)
        delivery = await delivery_service.create_delivery(
            sender_id=sender.id,
            pickup_address="הרצל 10, תל אביב",
            dropoff_address="בן יהודה 50, ירושלים",
            fee=10.0,
            station_id=station.id,
        )

        # אימות: broadcast לקבוצה בלבד (1 הודעה, לא 2)
        result = await db_session.execute(
            select(OutboxMessage).where(
                OutboxMessage.message_type == "delivery_broadcast"
            )
        )
        broadcasts = list(result.scalars().all())
        assert len(broadcasts) == 1
        assert broadcasts[0].recipient_id == "-100GROUP123"
        assert broadcasts[0].platform == MessagePlatform.TELEGRAM
