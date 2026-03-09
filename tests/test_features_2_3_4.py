"""
בדיקות לפיצ'רים 2, 3, 4:
- פיצ'ר 2: ביטול אוטומטי של משלוחים שלא נתפסו
- פיצ'ר 3: מנגנון retry חכם עם dead letter queue
- פיצ'ר 4: אימות חתימת webhook מקיף + חסימת IP אוטומטית
"""
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.outbox_message import OutboxMessage, MessagePlatform, MessageStatus
from app.db.models.user import User, UserRole
from app.core.config import settings


# ============================================================================
# פיצ'ר 2: ביטול אוטומטי של משלוחים שלא נתפסו
# ============================================================================


class TestAutoCancel:
    """בדיקות ביטול אוטומטי של משלוחים"""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_delivery_created_with_expires_at(self, db_session: AsyncSession):
        """משלוח חדש נוצר עם שדה expires_at"""
        from app.domain.services.delivery_service import DeliveryService

        # יצירת משתמש שולח
        sender = User(
            phone_number="+972501234567",
            name="שולח בדיקה",
            role=UserRole.SENDER,
            platform="telegram",
            is_active=True,
        )
        db_session.add(sender)
        await db_session.flush()

        service = DeliveryService(db_session)
        delivery = await service.create_delivery(
            sender_id=sender.id,
            pickup_address="תל אביב, רחוב דיזנגוף 1",
            dropoff_address="חיפה, רחוב הרצל 10",
            fee=25.0,
        )

        assert delivery.expires_at is not None
        # expires_at צריך להיות בערך AUTO_CANCEL_UNCAPTURED_HOURS שעות מעכשיו
        expected_expiry = datetime.utcnow() + timedelta(
            hours=settings.AUTO_CANCEL_UNCAPTURED_HOURS
        )
        diff = abs((delivery.expires_at - expected_expiry).total_seconds())
        assert diff < 5  # הפרש של פחות מ-5 שניות

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_expired_deliveries(self, db_session: AsyncSession):
        """שליפת משלוחים שפג תוקפם"""
        from app.domain.services.delivery_service import DeliveryService

        sender = User(
            phone_number="+972501111111",
            name="שולח",
            role=UserRole.SENDER,
            platform="telegram",
            is_active=True,
        )
        db_session.add(sender)
        await db_session.flush()

        # משלוח שפג תוקפו (expires_at בעבר)
        expired = Delivery(
            sender_id=sender.id,
            pickup_address="תל אביב",
            dropoff_address="ירושלים",
            status=DeliveryStatus.OPEN,
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        # משלוח שעדיין בתוקף
        active = Delivery(
            sender_id=sender.id,
            pickup_address="חיפה",
            dropoff_address="באר שבע",
            status=DeliveryStatus.OPEN,
            expires_at=datetime.utcnow() + timedelta(hours=10),
        )
        # משלוח שכבר נתפס (לא צריך להיכלל)
        captured = Delivery(
            sender_id=sender.id,
            pickup_address="נתניה",
            dropoff_address="אשדוד",
            status=DeliveryStatus.CAPTURED,
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        db_session.add_all([expired, active, captured])
        await db_session.flush()

        service = DeliveryService(db_session)
        result = await service.get_expired_deliveries()

        assert len(result) == 1
        assert result[0].id == expired.id

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_expiring_deliveries_warning(self, db_session: AsyncSession):
        """שליפת משלוחים שעומדים לפוג (לפני שליחת התראה)"""
        from app.domain.services.delivery_service import DeliveryService

        sender = User(
            phone_number="+972502222222",
            name="שולח",
            role=UserRole.SENDER,
            platform="whatsapp",
            is_active=True,
        )
        db_session.add(sender)
        await db_session.flush()

        # משלוח שעומד לפוג בעוד 20 דקות (בתוך חלון ה-30 דקות)
        expiring_soon = Delivery(
            sender_id=sender.id,
            pickup_address="רמת גן",
            dropoff_address="פתח תקווה",
            status=DeliveryStatus.OPEN,
            expires_at=datetime.utcnow() + timedelta(minutes=20),
        )
        # משלוח שפג כבר (לא צריך התראה — צריך ביטול)
        already_expired = Delivery(
            sender_id=sender.id,
            pickup_address="הרצליה",
            dropoff_address="כפר סבא",
            status=DeliveryStatus.OPEN,
            expires_at=datetime.utcnow() - timedelta(minutes=5),
        )
        # משלוח שכבר קיבל התראה
        warned = Delivery(
            sender_id=sender.id,
            pickup_address="ראשון",
            dropoff_address="חולון",
            status=DeliveryStatus.OPEN,
            expires_at=datetime.utcnow() + timedelta(minutes=15),
            expiry_warning_sent=datetime.utcnow() - timedelta(minutes=10),
        )
        db_session.add_all([expiring_soon, already_expired, warned])
        await db_session.flush()

        service = DeliveryService(db_session)
        result = await service.get_expiring_deliveries(warning_minutes=30)

        assert len(result) == 1
        assert result[0].id == expiring_soon.id

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_auto_cancel_delivery(self, db_session: AsyncSession):
        """ביטול אוטומטי של משלוח שפג תוקפו — ביצוע ידני (SQLite לא תומך ב-with_for_update)"""
        from app.domain.services.outbox_service import OutboxService

        sender = User(
            phone_number="+972503333333",
            name="שולח",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="123456",
            is_active=True,
        )
        db_session.add(sender)
        await db_session.flush()

        delivery = Delivery(
            sender_id=sender.id,
            pickup_address="תל אביב",
            dropoff_address="ירושלים",
            status=DeliveryStatus.OPEN,
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        db_session.add(delivery)
        await db_session.flush()

        # מבצעים את הלוגיקה ישירות (with_for_update לא נתמך ב-SQLite)
        delivery.status = DeliveryStatus.CANCELLED
        delivery.updated_at = datetime.utcnow()

        outbox_service = OutboxService(db_session)
        await outbox_service.queue_auto_cancel_notification(delivery)
        await db_session.commit()

        # ולידציה
        await db_session.refresh(delivery)
        assert delivery.status == DeliveryStatus.CANCELLED

        # בדיקה שנוצרה הודעת outbox להתראה
        outbox_result = await db_session.execute(
            select(OutboxMessage).where(
                OutboxMessage.message_type == "auto_cancel_notification"
            )
        )
        notification = outbox_result.scalar_one_or_none()
        assert notification is not None
        assert str(delivery.id) in notification.message_content.get("message_text", "")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_auto_cancel_ignores_captured_delivery(self, db_session: AsyncSession):
        """ביטול אוטומטי לא חל על משלוחים שכבר נתפסו"""
        from app.domain.services.delivery_service import DeliveryService

        sender = User(
            phone_number="+972504444444",
            name="שולח",
            role=UserRole.SENDER,
            platform="telegram",
            is_active=True,
        )
        db_session.add(sender)
        await db_session.flush()

        delivery = Delivery(
            sender_id=sender.id,
            pickup_address="תל אביב",
            dropoff_address="ירושלים",
            status=DeliveryStatus.CAPTURED,
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        db_session.add(delivery)
        await db_session.flush()

        service = DeliveryService(db_session)
        result = await service.auto_cancel_delivery(delivery.id)

        assert result is None  # לא בוטל


# ============================================================================
# פיצ'ר 3: מנגנון retry חכם עם Dead Letter Queue
# ============================================================================


class TestDeadLetterQueue:
    """בדיקות dead letter queue"""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_message_moves_to_dead_letter_after_max_retries(
        self, db_session: AsyncSession
    ):
        """הודעה שמיצתה את כל ניסיונות ה-retry עוברת ל-dead letter queue"""
        from app.domain.services.outbox_service import OutboxService
        from app.db.models.dead_letter_message import DeadLetterMessage

        # יצירת הודעת outbox
        message = OutboxMessage(
            platform=MessagePlatform.WHATSAPP,
            recipient_id="+972501234567",
            message_type="delivery_broadcast",
            message_content={"message_text": "בדיקה"},
            status=MessageStatus.PROCESSING,
            retry_count=2,  # כבר ניסתה 2 פעמים (max_retries=3)
            max_retries=3,
        )
        db_session.add(message)
        await db_session.flush()

        service = OutboxService(db_session)
        await service.mark_as_failed(message.id, "Connection refused")

        # ההודעה צריכה להיות בסטטוס FAILED
        await db_session.refresh(message)
        assert message.status == MessageStatus.FAILED

        # ההודעה צריכה להופיע ב-dead letter queue
        dl_result = await db_session.execute(
            select(DeadLetterMessage).where(
                DeadLetterMessage.original_message_id == message.id
            )
        )
        dead_letter = dl_result.scalar_one_or_none()
        assert dead_letter is not None
        assert dead_letter.failure_reason == "max_retries_exceeded"
        assert dead_letter.last_error == "Connection refused"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_permanent_error_goes_directly_to_dead_letter(
        self, db_session: AsyncSession
    ):
        """שגיאה קבועה (4xx) עוברת ישירות ל-dead letter ללא retry נוסף"""
        from app.domain.services.outbox_service import OutboxService
        from app.db.models.dead_letter_message import DeadLetterMessage

        message = OutboxMessage(
            platform=MessagePlatform.WHATSAPP,
            recipient_id="+972501234567",
            message_type="delivery_broadcast",
            message_content={"message_text": "בדיקה"},
            status=MessageStatus.PROCESSING,
            retry_count=0,
            max_retries=3,
        )
        db_session.add(message)
        await db_session.flush()

        service = OutboxService(db_session)
        await service.mark_as_failed(
            message.id, "400 Bad Request - invalid phone", is_transient=False
        )

        # ההודעה צריכה להיות FAILED מיד (לא PENDING)
        await db_session.refresh(message)
        assert message.status == MessageStatus.FAILED

        # צריכה להיות ב-dead letter
        dl_result = await db_session.execute(
            select(DeadLetterMessage).where(
                DeadLetterMessage.original_message_id == message.id
            )
        )
        dead_letter = dl_result.scalar_one_or_none()
        assert dead_letter is not None
        assert dead_letter.failure_reason == "permanent"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_transient_error_retries_before_dead_letter(
        self, db_session: AsyncSession
    ):
        """שגיאה זמנית מקבלת retry עם exponential backoff"""
        from app.domain.services.outbox_service import OutboxService

        message = OutboxMessage(
            platform=MessagePlatform.WHATSAPP,
            recipient_id="+972501234567",
            message_type="delivery_broadcast",
            message_content={"message_text": "בדיקה"},
            status=MessageStatus.PROCESSING,
            retry_count=0,
            max_retries=3,
        )
        db_session.add(message)
        await db_session.flush()

        service = OutboxService(db_session)
        await service.mark_as_failed(
            message.id, "502 Bad Gateway", is_transient=True
        )

        # ההודעה צריכה לחזור ל-PENDING (לא FAILED)
        await db_session.refresh(message)
        assert message.status == MessageStatus.PENDING
        assert message.retry_count == 1
        assert message.next_retry_at is not None

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_retry_dead_letter_message(self, db_session: AsyncSession):
        """שליחה חוזרת ידנית של הודעה מ-dead letter queue"""
        from app.domain.services.outbox_service import OutboxService
        from app.db.models.dead_letter_message import (
            DeadLetterMessage,
            DeadLetterStatus,
        )

        # יצירת הודעה ב-dead letter
        dead_letter = DeadLetterMessage(
            original_message_id=999,
            platform="whatsapp",
            recipient_id="+972501234567",
            message_type="delivery_broadcast",
            message_content={"message_text": "בדיקה"},
            retry_count=3,
            last_error="All retries failed",
            failure_reason="max_retries_exceeded",
            status=DeadLetterStatus.FAILED,
        )
        db_session.add(dead_letter)
        await db_session.flush()

        service = OutboxService(db_session)
        new_message = await service.retry_dead_letter(dead_letter.id)

        assert new_message is not None
        assert new_message.status == MessageStatus.PENDING
        assert new_message.retry_count == 0

        # ה-dead letter צריך להיות מסומן כ-retried
        await db_session.refresh(dead_letter)
        assert dead_letter.status == DeadLetterStatus.RETRIED
        assert dead_letter.retried_at is not None

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_dead_letter_count(self, db_session: AsyncSession):
        """ספירת הודעות כושלות ב-dead letter queue"""
        from app.domain.services.outbox_service import OutboxService
        from app.db.models.dead_letter_message import (
            DeadLetterMessage,
            DeadLetterStatus,
        )

        # יצירת 3 הודעות כושלות ו-1 שכבר עברה retry
        for i in range(3):
            db_session.add(DeadLetterMessage(
                original_message_id=i + 100,
                platform="telegram",
                recipient_id=f"chat_{i}",
                message_type="test",
                message_content={"message_text": f"test {i}"},
                status=DeadLetterStatus.FAILED,
            ))
        db_session.add(DeadLetterMessage(
            original_message_id=200,
            platform="whatsapp",
            recipient_id="+972501234567",
            message_type="test",
            message_content={"message_text": "retried"},
            status=DeadLetterStatus.RETRIED,
        ))
        await db_session.flush()

        service = OutboxService(db_session)
        count = await service.get_dead_letter_count()
        assert count == 3


# ============================================================================
# פיצ'ר 3: סיווג שגיאות — transient vs permanent
# ============================================================================


class TestErrorClassification:
    """בדיקות סיווג שגיאות (transient vs permanent)"""

    @pytest.mark.unit
    def test_transient_error_502(self):
        """שגיאת 502 מסווגת כ-transient"""
        from app.workers.tasks import _is_transient_error

        exc = Exception("Server error")
        exc.status_code = 502
        assert _is_transient_error(exc) is True

    @pytest.mark.unit
    def test_transient_error_429(self):
        """שגיאת 429 (rate limit) מסווגת כ-transient"""
        from app.workers.tasks import _is_transient_error

        exc = Exception("Rate limited")
        exc.status_code = 429
        assert _is_transient_error(exc) is True

    @pytest.mark.unit
    def test_permanent_error_400(self):
        """שגיאת 400 מסווגת כ-permanent"""
        from app.workers.tasks import _is_transient_error

        exc = Exception("Bad request")
        exc.status_code = 400
        assert _is_transient_error(exc) is False

    @pytest.mark.unit
    def test_permanent_error_404(self):
        """שגיאת 404 מסווגת כ-permanent"""
        from app.workers.tasks import _is_transient_error

        exc = Exception("Not found")
        exc.status_code = 404
        assert _is_transient_error(exc) is False

    @pytest.mark.unit
    def test_network_error_is_transient(self):
        """שגיאת רשת ללא קוד HTTP מסווגת כ-transient"""
        from app.workers.tasks import _is_transient_error

        exc = ConnectionError("Connection refused")
        assert _is_transient_error(exc) is True


# ============================================================================
# פיצ'ר 4: אימות חתימת webhook + חסימת IP אוטומטית
# ============================================================================


class TestWebhookSignatureVerification:
    """בדיקות אימות חתימת webhook"""

    @pytest.mark.unit
    def test_valid_wppconnect_signature(self):
        """חתימה תקינה של WPPConnect מאומתת בהצלחה"""
        from app.api.dependencies.webhook_signature import verify_wppconnect_signature

        secret = "test-secret-key"
        body = b'{"event": "message", "data": {}}'
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch.object(settings, "WPPCONNECT_WEBHOOK_SECRET", secret):
            assert verify_wppconnect_signature(body, f"sha256={expected}") is True

    @pytest.mark.unit
    def test_invalid_wppconnect_signature(self):
        """חתימה שגויה של WPPConnect נדחית"""
        from app.api.dependencies.webhook_signature import verify_wppconnect_signature

        secret = "test-secret-key"
        body = b'{"event": "message", "data": {}}'

        with patch.object(settings, "WPPCONNECT_WEBHOOK_SECRET", secret):
            assert verify_wppconnect_signature(body, "sha256=invalid_hex") is False

    @pytest.mark.unit
    def test_missing_wppconnect_signature(self):
        """חתימה חסרה נדחית כשסוד מוגדר"""
        from app.api.dependencies.webhook_signature import verify_wppconnect_signature

        with patch.object(settings, "WPPCONNECT_WEBHOOK_SECRET", "some-secret"):
            assert verify_wppconnect_signature(b"body", None) is False

    @pytest.mark.unit
    def test_no_secret_configured_allows_all(self):
        """כשסוד לא מוגדר — כל הבקשות מתקבלות"""
        from app.api.dependencies.webhook_signature import verify_wppconnect_signature

        with patch.object(settings, "WPPCONNECT_WEBHOOK_SECRET", ""):
            assert verify_wppconnect_signature(b"body", None) is True


class TestIPBlocking:
    """בדיקות חסימת IP אוטומטית"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ip_blocked_after_threshold(self):
        """IP נחסם אחרי X ניסיונות כושלים"""
        from app.api.dependencies.webhook_signature import (
            _record_failed_attempt,
            _is_ip_blocked,
            _failed_attempts,
            _blocked_ips,
        )

        test_ip = "192.168.1.100"

        # ניקוי מצב קודם
        _failed_attempts.pop(test_ip, None)
        _blocked_ips.pop(test_ip, None)

        try:
            # כופה fallback לזיכרון מקומי (בלי Redis)
            with patch("app.api.dependencies.webhook_signature._get_redis_safe", new=AsyncMock(return_value=None)):
                assert await _is_ip_blocked(test_ip) is False

                # רישום ניסיונות כושלים עד לסף
                for _ in range(settings.WEBHOOK_SIGNATURE_BLOCK_AFTER):
                    await _record_failed_attempt(test_ip)

                assert await _is_ip_blocked(test_ip) is True
        finally:
            # ניקוי
            _failed_attempts.pop(test_ip, None)
            _blocked_ips.pop(test_ip, None)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ip_unblocked_after_duration(self):
        """IP משוחרר אחרי תקופת החסימה"""
        from app.api.dependencies.webhook_signature import (
            _is_ip_blocked,
            _blocked_ips,
            _failed_attempts,
        )

        test_ip = "10.0.0.99"

        # ניקוי מצב קודם
        _failed_attempts.pop(test_ip, None)
        _blocked_ips.pop(test_ip, None)

        try:
            # חסימה שפגה (timestamp בעבר)
            _blocked_ips[test_ip] = time.time() - 1

            # כופה fallback לזיכרון מקומי (בלי Redis)
            with patch("app.api.dependencies.webhook_signature._get_redis_safe", new=AsyncMock(return_value=None)):
                assert await _is_ip_blocked(test_ip) is False
                assert test_ip not in _blocked_ips  # נוקה אוטומטית
        finally:
            _failed_attempts.pop(test_ip, None)
            _blocked_ips.pop(test_ip, None)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_blocked_ips(self):
        """שליפת רשימת IP חסומים"""
        from app.api.dependencies.webhook_signature import (
            get_blocked_ips,
            _blocked_ips,
            _failed_attempts,
        )

        test_ip1 = "172.16.0.1"
        test_ip2 = "172.16.0.2"

        _failed_attempts.pop(test_ip1, None)
        _failed_attempts.pop(test_ip2, None)
        _blocked_ips.pop(test_ip1, None)
        _blocked_ips.pop(test_ip2, None)

        try:
            _blocked_ips[test_ip1] = time.time() + 3600
            _blocked_ips[test_ip2] = time.time() - 10  # פגה

            # כופה fallback לזיכרון מקומי (בלי Redis)
            with patch("app.api.dependencies.webhook_signature._get_redis_safe", new=AsyncMock(return_value=None)):
                result = await get_blocked_ips()
                assert test_ip1 in result
                assert test_ip2 not in result  # נוקה כי פגה
        finally:
            _blocked_ips.pop(test_ip1, None)
            _blocked_ips.pop(test_ip2, None)
            _failed_attempts.pop(test_ip1, None)
            _failed_attempts.pop(test_ip2, None)

    @pytest.mark.unit
    def test_x_forwarded_for_ignored_without_trusted_proxy(self):
        """X-Forwarded-For מתעלם כשאין proxy מהימן מוגדר"""
        from unittest.mock import MagicMock
        from app.api.dependencies.webhook_signature import _get_client_ip

        request = MagicMock()
        request.client.host = "1.2.3.4"
        request.headers.get.return_value = "10.0.0.1"  # X-Forwarded-For מזויף

        with patch.object(settings, "TRUSTED_PROXY_IPS", ""):
            ip = _get_client_ip(request)
            assert ip == "1.2.3.4"  # חייב להחזיר IP ישיר, לא הכותרת המזויפת

    @pytest.mark.unit
    def test_x_forwarded_for_trusted_when_proxy_configured(self):
        """X-Forwarded-For נסמך כשהבקשה מגיעה מ-proxy מהימן"""
        from unittest.mock import MagicMock
        from app.api.dependencies.webhook_signature import _get_client_ip

        request = MagicMock()
        request.client.host = "10.0.0.50"  # ה-proxy
        request.headers.get.return_value = "203.0.113.5"  # IP אמיתי

        with patch.object(settings, "TRUSTED_PROXY_IPS", "10.0.0.50,10.0.0.51"):
            ip = _get_client_ip(request)
            assert ip == "203.0.113.5"  # סומך על X-Forwarded-For כי ה-proxy מהימן

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_failed_attempt_counts(self):
        """שליפת מונה ניסיונות כושלים לכל IP"""
        from app.api.dependencies.webhook_signature import (
            get_failed_attempt_counts,
            _failed_attempts,
        )

        test_ip = "192.168.99.99"
        _failed_attempts.pop(test_ip, None)

        try:
            _failed_attempts[test_ip] = [time.time(), time.time()]
            # כופה fallback לזיכרון מקומי (בלי Redis)
            with patch("app.api.dependencies.webhook_signature._get_redis_safe", new=AsyncMock(return_value=None)):
                result = await get_failed_attempt_counts()
                assert result.get(test_ip) == 2
        finally:
            _failed_attempts.pop(test_ip, None)
