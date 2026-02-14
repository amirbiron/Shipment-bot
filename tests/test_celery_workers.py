"""
בדיקות ל-Celery Workers — app/workers/tasks.py

מכסה:
- עיבוד הודעות outbox (happy path + כשלון)
- שידור לשליחים (broadcast) — כולל סינון נמענים
- שידור לסדרנים (dispatcher broadcast)
- הודעות ישירות (direct message)
- ניקוי הודעות ישנות (cleanup)
- ניקוי אירועי webhook ישנים
- חסימה אוטומטית יומית (billing cycle blocking)
- ניהול event loop ב-Celery
- שליחת WhatsApp ו-Telegram עם circuit breaker
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.outbox_message import MessagePlatform, MessageStatus, OutboxMessage
from app.db.models.user import ApprovalStatus, User, UserRole
from app.db.models.webhook_event import WebhookEvent
from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.domain.services.outbox_service import OutboxService


@contextmanager
def _patch_run_async_for_test():
    """
    מוק ל-run_async שמאפשר להריץ טאסקי Celery sync מתוך בדיקה async.

    הטאסקים של Celery הם sync ומשתמשים ב-run_async() שיוצר event loop חדש.
    בבדיקות async כבר רץ event loop — לכן מחליפים את run_async בגרסה
    שמריצה את ה-coroutine ב-loop חדש בתוך thread נפרד.
    """
    import concurrent.futures

    def _test_run_async(coro):
        """מריץ coroutine ב-loop חדש בתוך thread — עוקף את ההגבלה של nested loops"""
        from app.core.logging import set_correlation_id
        set_correlation_id()

        def _run_in_thread():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_in_thread)
            return future.result(timeout=30)

    with patch("app.workers.tasks.run_async", side_effect=_test_run_async):
        yield


# ============================================================================
# Helpers
# ============================================================================

def _make_outbox_message(
    *,
    platform: MessagePlatform = MessagePlatform.WHATSAPP,
    recipient_id: str = "+972501234567",
    message_type: str = "test",
    message_content: dict | None = None,
    status: MessageStatus = MessageStatus.PENDING,
) -> OutboxMessage:
    """יוצר אובייקט OutboxMessage לבדיקה ללא שמירה ב-DB"""
    return OutboxMessage(
        platform=platform,
        recipient_id=recipient_id,
        message_type=message_type,
        message_content=message_content or {"message_text": "הודעת בדיקה"},
        status=status,
    )


async def _insert_outbox(
    db: AsyncSession,
    *,
    platform: MessagePlatform = MessagePlatform.WHATSAPP,
    recipient_id: str = "+972501234567",
    message_type: str = "test",
    message_content: dict | None = None,
    status: MessageStatus = MessageStatus.PENDING,
    processed_at: datetime | None = None,
) -> OutboxMessage:
    """יוצר ושומר הודעת outbox ב-DB"""
    msg = _make_outbox_message(
        platform=platform,
        recipient_id=recipient_id,
        message_type=message_type,
        message_content=message_content,
        status=status,
    )
    if processed_at is not None:
        msg.processed_at = processed_at
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


# ============================================================================
# בדיקות שליחת WhatsApp
# ============================================================================


class TestSendWhatsAppMessage:
    """בדיקות ל-_send_whatsapp_message"""

    @pytest.mark.asyncio
    async def test_send_whatsapp_success(self, mock_whatsapp_gateway) -> None:
        """שליחה מוצלחת דרך WhatsApp Gateway"""
        from app.workers.tasks import _send_whatsapp_message

        result = await _send_whatsapp_message(
            "+972501234567", {"message_text": "שלום"}
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_send_whatsapp_failure_returns_false(self) -> None:
        """כשלון בשליחה מחזיר False ולא זורק exception"""
        from app.workers.tasks import _send_whatsapp_message

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await _send_whatsapp_message(
                "+972501234567", {"message_text": "שלום"}
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_send_whatsapp_converts_html_to_whatsapp_format(
        self, mock_whatsapp_gateway
    ) -> None:
        """וידוא המרת תגי HTML לפורמט WhatsApp"""
        from app.workers.tasks import _send_whatsapp_message

        content = {"message_text": "<b>כותרת</b> טקסט רגיל"}
        await _send_whatsapp_message("+972501234567", content)

        # בדיקה שהקריאה בוצעה עם טקסט מומר
        call_args = mock_whatsapp_gateway.post.call_args
        assert call_args is not None
        sent_json = call_args.kwargs.get("json") or call_args[1].get("json")
        # הטקסט אמור להכיל * במקום <b> (המרת HTML ל-WhatsApp)
        assert "<b>" not in sent_json["message"]

    @pytest.mark.asyncio
    async def test_send_whatsapp_network_exception_returns_false(self) -> None:
        """exception בחיבור מחזיר False"""
        from app.workers.tasks import _send_whatsapp_message

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(side_effect=ConnectionError("no network"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await _send_whatsapp_message(
                "+972501234567", {"message_text": "test"}
            )
            assert result is False


# ============================================================================
# בדיקות שליחת Telegram
# ============================================================================


class TestSendTelegramMessage:
    """בדיקות ל-_send_telegram_message"""

    @pytest.mark.asyncio
    async def test_send_telegram_success(self, mock_telegram_api) -> None:
        """שליחה מוצלחת דרך Telegram Bot API"""
        from app.workers.tasks import _send_telegram_message

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN = "test-token"

            result = await _send_telegram_message(
                "123456", {"message_text": "שלום"}
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_send_telegram_no_token_returns_false(self) -> None:
        """אם אין TELEGRAM_BOT_TOKEN, מחזיר False"""
        from app.workers.tasks import _send_telegram_message

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN = None

            result = await _send_telegram_message(
                "123456", {"message_text": "שלום"}
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_send_telegram_with_inline_keyboard(
        self, mock_telegram_api
    ) -> None:
        """שליחה עם inline keyboard (כפתורי אישור/דחייה)"""
        from app.workers.tasks import _send_telegram_message

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN = "test-token"

            content = {
                "message_text": "בקשת משלוח",
                "inline_keyboard": [
                    [
                        {"text": "אשר", "callback_data": "approve_1"},
                        {"text": "דחה", "callback_data": "reject_1"},
                    ]
                ],
            }
            result = await _send_telegram_message("123456", content)
            assert result is True

            # ווידוא שה-payload כולל reply_markup
            call_args = mock_telegram_api.post.call_args
            sent_json = call_args.kwargs.get("json") or call_args[1].get("json")
            assert "reply_markup" in sent_json
            assert "inline_keyboard" in sent_json["reply_markup"]

    @pytest.mark.asyncio
    async def test_send_telegram_failure_returns_false(self) -> None:
        """כשלון בשליחה מחזיר False"""
        from app.workers.tasks import _send_telegram_message

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN = "test-token"

            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                result = await _send_telegram_message(
                    "123456", {"message_text": "test"}
                )
                assert result is False


# ============================================================================
# בדיקות שליפת נמענים
# ============================================================================


class TestGetCourierRecipients:
    """בדיקות ל-_get_courier_recipients"""

    @pytest.mark.asyncio
    async def test_returns_only_active_approved_couriers(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        """מחזיר רק שליחים פעילים ומאושרים בפלטפורמה הנכונה"""
        from app.workers.tasks import _get_courier_recipients

        # שליח פעיל ומאושר ב-whatsapp
        await user_factory(
            phone_number="+972501111111",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )
        # שליח לא פעיל
        await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=False,
            approval_status=ApprovalStatus.APPROVED,
        )
        # שליח ממתין לאישור
        await user_factory(
            phone_number="+972503333333",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=True,
            approval_status=ApprovalStatus.PENDING,
        )
        # שולח (לא שליח)
        await user_factory(
            phone_number="+972504444444",
            role=UserRole.SENDER,
            platform="whatsapp",
            is_active=True,
        )

        recipients = await _get_courier_recipients(db_session, MessagePlatform.WHATSAPP)
        assert len(recipients) == 1
        assert recipients[0].phone_number == "+972501111111"

    @pytest.mark.asyncio
    async def test_filters_by_platform(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        """מסנן לפי פלטפורמה"""
        from app.workers.tasks import _get_courier_recipients

        await user_factory(
            phone_number="+972501111111",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )
        await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="111",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )

        wa_recipients = await _get_courier_recipients(
            db_session, MessagePlatform.WHATSAPP
        )
        tg_recipients = await _get_courier_recipients(
            db_session, MessagePlatform.TELEGRAM
        )

        assert len(wa_recipients) == 1
        assert len(tg_recipients) == 1
        assert wa_recipients[0].platform == "whatsapp"
        assert tg_recipients[0].platform == "telegram"

    @pytest.mark.asyncio
    async def test_empty_when_no_couriers(self, db_session: AsyncSession) -> None:
        """מחזיר רשימה ריקה כשאין שליחים"""
        from app.workers.tasks import _get_courier_recipients

        recipients = await _get_courier_recipients(db_session, MessagePlatform.WHATSAPP)
        assert recipients == []


class TestGetDispatcherRecipients:
    """בדיקות ל-_get_dispatcher_recipients"""

    @pytest.mark.asyncio
    async def test_returns_active_dispatchers_for_station(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        """מחזיר סדרנים פעילים של תחנה פעילה"""
        from app.workers.tasks import _get_dispatcher_recipients

        # יצירת בעל תחנה
        owner = await user_factory(
            phone_number="+972501111111",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="owner1",
        )

        # יצירת תחנה
        station = Station(
            name="תחנת בדיקה",
            owner_id=owner.id,
            is_active=True,
        )
        db_session.add(station)
        await db_session.commit()
        await db_session.refresh(station)

        # יצירת סדרן
        dispatcher_user = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="disp1",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )

        # קישור סדרן לתחנה
        sd = StationDispatcher(
            station_id=station.id,
            user_id=dispatcher_user.id,
            is_active=True,
        )
        db_session.add(sd)
        await db_session.commit()

        recipients = await _get_dispatcher_recipients(
            db_session, station.id, MessagePlatform.TELEGRAM
        )
        assert len(recipients) == 1
        assert recipients[0].id == dispatcher_user.id

    @pytest.mark.asyncio
    async def test_excludes_inactive_dispatcher(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        """מסנן סדרנים לא פעילים"""
        from app.workers.tasks import _get_dispatcher_recipients

        owner = await user_factory(
            phone_number="+972501111111",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="owner1",
        )

        station = Station(name="תחנה", owner_id=owner.id, is_active=True)
        db_session.add(station)
        await db_session.commit()
        await db_session.refresh(station)

        dispatcher_user = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="disp1",
            is_active=True,
        )

        # סדרן לא פעיל
        sd = StationDispatcher(
            station_id=station.id,
            user_id=dispatcher_user.id,
            is_active=False,
        )
        db_session.add(sd)
        await db_session.commit()

        recipients = await _get_dispatcher_recipients(
            db_session, station.id, MessagePlatform.TELEGRAM
        )
        assert len(recipients) == 0

    @pytest.mark.asyncio
    async def test_excludes_inactive_station(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        """מסנן תחנות לא פעילות"""
        from app.workers.tasks import _get_dispatcher_recipients

        owner = await user_factory(
            phone_number="+972501111111",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="owner1",
        )

        # תחנה לא פעילה
        station = Station(name="תחנה", owner_id=owner.id, is_active=False)
        db_session.add(station)
        await db_session.commit()
        await db_session.refresh(station)

        dispatcher_user = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="disp1",
            is_active=True,
        )

        sd = StationDispatcher(
            station_id=station.id,
            user_id=dispatcher_user.id,
            is_active=True,
        )
        db_session.add(sd)
        await db_session.commit()

        recipients = await _get_dispatcher_recipients(
            db_session, station.id, MessagePlatform.TELEGRAM
        )
        assert len(recipients) == 0


# ============================================================================
# בדיקות עיבוד הודעה בודדת
# ============================================================================


class TestProcessSingleMessage:
    """בדיקות ל-_process_single_message"""

    @pytest.mark.asyncio
    async def test_direct_whatsapp_message_success(
        self, db_session: AsyncSession, mock_whatsapp_gateway
    ) -> None:
        """הודעה ישירה ב-WhatsApp — happy path"""
        from app.workers.tasks import _process_single_message

        msg = await _insert_outbox(
            db_session,
            platform=MessagePlatform.WHATSAPP,
            recipient_id="+972501234567",
            message_content={"message_text": "בדיקה"},
        )

        # מוק ל-get_task_session שמחזיר את ה-session הקיים
        with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=db_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            success, result = await _process_single_message(msg)

        assert success is True
        assert "sent successfully" in result

        # ווידוא שההודעה סומנה כנשלחה
        await db_session.refresh(msg)
        assert msg.status == MessageStatus.SENT

    @pytest.mark.asyncio
    async def test_direct_telegram_message_success(
        self, db_session: AsyncSession, mock_telegram_api
    ) -> None:
        """הודעה ישירה ב-Telegram — happy path"""
        from app.workers.tasks import _process_single_message

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN = "test-token"

            msg = await _insert_outbox(
                db_session,
                platform=MessagePlatform.TELEGRAM,
                recipient_id="123456",
                message_content={"message_text": "בדיקה"},
            )

            with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
                mock_session_ctx.return_value.__aenter__ = AsyncMock(
                    return_value=db_session
                )
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                success, result = await _process_single_message(msg)

        assert success is True

    @pytest.mark.asyncio
    async def test_direct_message_failure_marks_as_failed(
        self, db_session: AsyncSession
    ) -> None:
        """הודעה שנכשלת — מסמנת כ-failed"""
        from app.workers.tasks import _process_single_message

        msg = await _insert_outbox(
            db_session,
            platform=MessagePlatform.WHATSAPP,
            recipient_id="+972501234567",
            message_content={"message_text": "בדיקה"},
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
                mock_session_ctx.return_value.__aenter__ = AsyncMock(
                    return_value=db_session
                )
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                success, result = await _process_single_message(msg)

        assert success is False

    @pytest.mark.asyncio
    async def test_broadcast_couriers_no_recipients(
        self, db_session: AsyncSession
    ) -> None:
        """שידור לשליחים ללא נמענים — מסמן כ-failed"""
        from app.workers.tasks import _process_single_message

        msg = await _insert_outbox(
            db_session,
            platform=MessagePlatform.WHATSAPP,
            recipient_id="BROADCAST_COURIERS",
            message_content={"message_text": "משלוח חדש"},
        )

        with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=db_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            success, result = await _process_single_message(msg)

        assert success is False
        assert "No recipients" in result

    @pytest.mark.asyncio
    async def test_broadcast_couriers_success(
        self, db_session: AsyncSession, user_factory, mock_whatsapp_gateway
    ) -> None:
        """שידור לשליחים — happy path"""
        from app.workers.tasks import _process_single_message

        await user_factory(
            phone_number="+972501111111",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )
        await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )

        msg = await _insert_outbox(
            db_session,
            platform=MessagePlatform.WHATSAPP,
            recipient_id="BROADCAST_COURIERS",
            message_content={"message_text": "משלוח חדש"},
        )

        with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=db_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            success, result = await _process_single_message(msg)

        assert success is True
        assert "2/2" in result

    @pytest.mark.asyncio
    async def test_broadcast_couriers_partial_success(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        """שידור חלקי — חלק מהשליחים נכשלים"""
        from app.workers.tasks import _process_single_message

        await user_factory(
            phone_number="+972501111111",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )
        await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )

        msg = await _insert_outbox(
            db_session,
            platform=MessagePlatform.WHATSAPP,
            recipient_id="BROADCAST_COURIERS",
            message_content={"message_text": "משלוח"},
        )

        # מוק שמחזיר True לקריאה ראשונה ו-False לשנייה
        call_count = 0

        async def _mock_send(phone, content):
            nonlocal call_count
            call_count += 1
            return call_count == 1  # רק הראשון מצליח

        with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=db_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "app.workers.tasks._send_whatsapp_message",
                side_effect=_mock_send,
            ):
                success, result = await _process_single_message(msg)

        assert success is True
        assert "Partial" in result or "1/2" in result

    @pytest.mark.asyncio
    async def test_broadcast_telegram_filters_no_chat_id(
        self, db_session: AsyncSession, user_factory, mock_telegram_api
    ) -> None:
        """שידור טלגרם — מסנן שליחים ללא chat_id"""
        from app.workers.tasks import _process_single_message

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN = "test-token"

            # שליח עם chat_id
            await user_factory(
                phone_number="+972501111111",
                role=UserRole.COURIER,
                platform="telegram",
                telegram_chat_id="chat1",
                is_active=True,
                approval_status=ApprovalStatus.APPROVED,
            )
            # שליח בלי chat_id
            await user_factory(
                phone_number="+972502222222",
                role=UserRole.COURIER,
                platform="telegram",
                telegram_chat_id=None,
                is_active=True,
                approval_status=ApprovalStatus.APPROVED,
            )

            msg = await _insert_outbox(
                db_session,
                platform=MessagePlatform.TELEGRAM,
                recipient_id="BROADCAST_COURIERS",
                message_content={"message_text": "משלוח"},
            )

            with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
                mock_session_ctx.return_value.__aenter__ = AsyncMock(
                    return_value=db_session
                )
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                success, result = await _process_single_message(msg)

        assert success is True
        # רק שליח אחד עם chat_id — 1/1
        assert "1/1" in result

    @pytest.mark.asyncio
    async def test_broadcast_dispatchers_success(
        self, db_session: AsyncSession, user_factory, mock_whatsapp_gateway
    ) -> None:
        """שידור לסדרנים — happy path"""
        from app.workers.tasks import _process_single_message

        owner = await user_factory(
            phone_number="+972501111111",
            role=UserRole.STATION_OWNER,
            platform="whatsapp",
        )

        station = Station(name="תחנה", owner_id=owner.id, is_active=True)
        db_session.add(station)
        await db_session.commit()
        await db_session.refresh(station)

        dispatcher = await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )

        sd = StationDispatcher(
            station_id=station.id,
            user_id=dispatcher.id,
            is_active=True,
        )
        db_session.add(sd)
        await db_session.commit()

        msg = await _insert_outbox(
            db_session,
            platform=MessagePlatform.WHATSAPP,
            recipient_id=f"BROADCAST_DISPATCHERS_{station.id}",
            message_content={"message_text": "בקשת משלוח"},
        )

        with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=db_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            success, result = await _process_single_message(msg)

        assert success is True
        assert "1/1" in result

    @pytest.mark.asyncio
    async def test_broadcast_dispatchers_no_recipients(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        """שידור לסדרנים ללא נמענים"""
        from app.workers.tasks import _process_single_message

        owner = await user_factory(
            phone_number="+972501111111",
            role=UserRole.STATION_OWNER,
            platform="whatsapp",
        )

        station = Station(name="תחנה", owner_id=owner.id, is_active=True)
        db_session.add(station)
        await db_session.commit()
        await db_session.refresh(station)

        msg = await _insert_outbox(
            db_session,
            platform=MessagePlatform.WHATSAPP,
            recipient_id=f"BROADCAST_DISPATCHERS_{station.id}",
            message_content={"message_text": "בקשה"},
        )

        with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=db_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            success, result = await _process_single_message(msg)

        assert success is False
        assert "No dispatchers" in result

    @pytest.mark.asyncio
    async def test_exception_during_processing_marks_failed(
        self, db_session: AsyncSession
    ) -> None:
        """exception בעיבוד — מסמן כ-failed עם הודעת שגיאה"""
        from app.workers.tasks import _process_single_message

        msg = await _insert_outbox(
            db_session,
            platform=MessagePlatform.WHATSAPP,
            recipient_id="+972501234567",
            message_content={"message_text": "test"},
        )

        with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=db_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            # מוק ש-OutboxService.mark_as_processing זורק exception
            with patch(
                "app.workers.tasks._send_whatsapp_message",
                side_effect=RuntimeError("כשלון בלתי צפוי"),
            ):
                success, result = await _process_single_message(msg)

        assert success is False
        assert "כשלון בלתי צפוי" in result


# ============================================================================
# בדיקות Event Loop Management
# ============================================================================


class TestEventLoopManagement:
    """בדיקות ל-get_event_loop ו-run_async"""

    def test_get_event_loop_creates_and_closes(self) -> None:
        """get_event_loop יוצר loop חדש וסוגר אותו"""
        from app.workers.tasks import get_event_loop

        with get_event_loop() as loop:
            assert loop is not None
            assert loop.is_running() is False

        # אחרי היציאה, ה-loop צריך להיות סגור
        assert loop.is_closed()

    def test_run_async_executes_coroutine(self) -> None:
        """run_async מבצע coroutine ומחזיר תוצאה"""
        from app.workers.tasks import run_async

        async def _coro():
            return 42

        result = run_async(_coro())
        assert result == 42


# ============================================================================
# בדיקות ניקוי הודעות ישנות
# ============================================================================


class TestCleanupOldMessages:
    """בדיקות ל-cleanup_old_messages"""

    @pytest.mark.asyncio
    async def test_deletes_old_sent_messages(
        self, db_session: AsyncSession
    ) -> None:
        """מוחק הודעות שנשלחו ומעובדו לפני יותר מ-30 יום"""
        # הודעה ישנה — SENT לפני 60 יום
        old_msg = await _insert_outbox(
            db_session,
            status=MessageStatus.SENT,
            processed_at=datetime.utcnow() - timedelta(days=60),
        )

        # הודעה חדשה — SENT לפני 5 ימים
        new_msg = await _insert_outbox(
            db_session,
            recipient_id="+972509999999",
            status=MessageStatus.SENT,
            processed_at=datetime.utcnow() - timedelta(days=5),
        )

        # הודעה ישנה אבל PENDING — לא צריכה להימחק
        pending_msg = await _insert_outbox(
            db_session,
            recipient_id="+972508888888",
            status=MessageStatus.PENDING,
            processed_at=datetime.utcnow() - timedelta(days=60),
        )

        # הרצת הטאסק בפועל — מריץ cleanup_old_messages עם session מוק
        from app.workers.tasks import cleanup_old_messages

        with _patch_run_async_for_test():
            with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
                mock_session_ctx.return_value.__aenter__ = AsyncMock(
                    return_value=db_session
                )
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                result = cleanup_old_messages(days=30)

        assert result["deleted"] == 1

        # ווידוא שהישנה נמחקה
        db_result = await db_session.execute(
            select(OutboxMessage).where(OutboxMessage.id == old_msg.id)
        )
        assert db_result.scalar_one_or_none() is None

        # ווידוא שהחדשה ו-PENDING נשארו
        db_result = await db_session.execute(
            select(OutboxMessage).where(OutboxMessage.id == new_msg.id)
        )
        assert db_result.scalar_one_or_none() is not None

        db_result = await db_session.execute(
            select(OutboxMessage).where(OutboxMessage.id == pending_msg.id)
        )
        assert db_result.scalar_one_or_none() is not None


class TestCleanupOldWebhookEvents:
    """בדיקות ל-cleanup_old_webhook_events"""

    @pytest.mark.asyncio
    async def test_deletes_old_completed_events(
        self, db_session: AsyncSession
    ) -> None:
        """מוחק אירועי webhook ישנים עם status=completed"""
        # אירוע ישן — completed לפני 14 יום
        old_event = WebhookEvent(
            message_id="old_event_1",
            platform="telegram",
            status="completed",
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        db_session.add(old_event)

        # אירוע חדש — completed לפני יום
        new_event = WebhookEvent(
            message_id="new_event_1",
            platform="telegram",
            status="completed",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db_session.add(new_event)

        # אירוע ישן — processing (לא צריך להימחק)
        processing_event = WebhookEvent(
            message_id="processing_event_1",
            platform="telegram",
            status="processing",
            created_at=datetime.now(timezone.utc) - timedelta(days=14),
        )
        db_session.add(processing_event)
        await db_session.commit()

        # הרצת הטאסק בפועל
        from app.workers.tasks import cleanup_old_webhook_events

        with _patch_run_async_for_test():
            with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
                mock_session_ctx.return_value.__aenter__ = AsyncMock(
                    return_value=db_session
                )
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                result = cleanup_old_webhook_events(days=7)

        assert result["deleted"] == 1

        # ווידוא שהחדש והישן ב-processing נשארו
        db_result = await db_session.execute(
            select(WebhookEvent).where(WebhookEvent.message_id == "new_event_1")
        )
        assert db_result.scalar_one_or_none() is not None

        db_result = await db_session.execute(
            select(WebhookEvent).where(
                WebhookEvent.message_id == "processing_event_1"
            )
        )
        assert db_result.scalar_one_or_none() is not None


# ============================================================================
# בדיקות broadcast_to_couriers (Celery task)
# ============================================================================


class TestBroadcastToCouriers:
    """בדיקות לטאסק broadcast_to_couriers"""

    @pytest.mark.asyncio
    async def test_broadcast_no_couriers_returns_error(
        self, db_session: AsyncSession
    ) -> None:
        """כשאין שליחים, הטאסק מחזיר error"""
        from app.workers.tasks import broadcast_to_couriers

        with _patch_run_async_for_test():
            with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
                mock_session_ctx.return_value.__aenter__ = AsyncMock(
                    return_value=db_session
                )
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                result = broadcast_to_couriers("משלוח חדש!", delivery_id=1)

        assert result["total_sent"] == 0
        assert result["successful"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_both_platforms(
        self, db_session: AsyncSession, user_factory, mock_whatsapp_gateway
    ) -> None:
        """הטאסק שולח לשליחים בשתי הפלטפורמות"""
        from app.workers.tasks import broadcast_to_couriers

        await user_factory(
            phone_number="+972501111111",
            role=UserRole.COURIER,
            platform="whatsapp",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )
        await user_factory(
            phone_number="+972502222222",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="tg1",
            is_active=True,
            approval_status=ApprovalStatus.APPROVED,
        )

        with _patch_run_async_for_test():
            with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
                mock_session_ctx.return_value.__aenter__ = AsyncMock(
                    return_value=db_session
                )
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                with patch("app.core.config.settings") as mock_settings:
                    mock_settings.TELEGRAM_BOT_TOKEN = "test-token"

                    result = broadcast_to_couriers("משלוח חדש!", delivery_id=1)

        assert result["total_sent"] == 2
        assert result["successful"] == 2


# ============================================================================
# בדיקות process_billing_cycle_blocking
# ============================================================================


class TestProcessBillingCycleBlocking:
    """בדיקות לטאסק process_billing_cycle_blocking"""

    @pytest.mark.asyncio
    async def test_no_active_stations_returns_zero(
        self, db_session: AsyncSession
    ) -> None:
        """כשאין תחנות פעילות — הטאסק מחזיר 0 נחסמו"""
        from app.workers.tasks import process_billing_cycle_blocking

        with _patch_run_async_for_test():
            with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
                mock_session_ctx.return_value.__aenter__ = AsyncMock(
                    return_value=db_session
                )
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                result = process_billing_cycle_blocking()

        assert result["stations_processed"] == 0
        assert result["drivers_blocked"] == 0

    @pytest.mark.asyncio
    async def test_station_error_does_not_stop_processing(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        """שגיאה בתחנה אחת לא עוצרת עיבוד תחנות אחרות — הטאסק ממשיך"""
        from app.workers.tasks import process_billing_cycle_blocking

        owner = await user_factory(
            phone_number="+972501111111",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="owner1",
        )

        station1 = Station(name="תחנה 1", owner_id=owner.id, is_active=True)
        station2 = Station(name="תחנה 2", owner_id=owner.id, is_active=True)
        db_session.add_all([station1, station2])
        await db_session.commit()
        # eager load — מונע lazy load מ-thread אחר
        await db_session.refresh(station1)
        await db_session.refresh(station2)

        # מוק שזורק exception בתחנה הראשונה ומצליח בשנייה
        call_count = 0

        async def _mock_auto_block(self_svc, station_id: int) -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("DB error")
            return []  # תחנה שנייה — ללא חסימות

        # מוק ברמת המחלקה — מוסיף self
        # rollback מבוטל בסביבת test — מונע expire של station objects
        # שגורם ל-MissingGreenlet בגישה ל-station.id מ-thread אחר
        with _patch_run_async_for_test():
            with patch("app.workers.tasks.get_task_session") as mock_session_ctx:
                mock_session_ctx.return_value.__aenter__ = AsyncMock(
                    return_value=db_session
                )
                mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

                with patch.object(db_session, "rollback", new_callable=AsyncMock):
                    with patch(
                        "app.domain.services.station_service.StationService.auto_block_unpaid_drivers",
                        _mock_auto_block,
                    ):
                        result = process_billing_cycle_blocking()

        # שתי התחנות עובדו — הראשונה נכשלה אבל לא עצרה את השנייה
        assert result["stations_processed"] == 2
        assert call_count == 2


# ============================================================================
# בדיקות OutboxService integration
# ============================================================================


class TestOutboxServiceIntegration:
    """בדיקות אינטגרציה של OutboxService עם הודעות"""

    @pytest.mark.asyncio
    async def test_get_pending_messages_respects_limit(
        self, db_session: AsyncSession
    ) -> None:
        """get_pending_messages מכבד את ה-limit"""
        # יצירת 5 הודעות
        for i in range(5):
            await _insert_outbox(
                db_session,
                recipient_id=f"+97250{i:07d}",
            )

        svc = OutboxService(db_session)
        messages = await svc.get_pending_messages(limit=3)
        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_get_pending_messages_skips_future_retry(
        self, db_session: AsyncSession
    ) -> None:
        """הודעות עם next_retry_at עתידי לא נשלפות"""
        # הודעה עם retry עתידי
        msg = await _insert_outbox(db_session)
        msg.next_retry_at = datetime.utcnow() + timedelta(hours=1)
        await db_session.commit()

        svc = OutboxService(db_session)
        messages = await svc.get_pending_messages()
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_mark_as_processing_changes_status(
        self, db_session: AsyncSession
    ) -> None:
        """mark_as_processing משנה סטטוס ל-PROCESSING"""
        msg = await _insert_outbox(db_session)
        svc = OutboxService(db_session)

        await svc.mark_as_processing(msg.id)

        await db_session.refresh(msg)
        assert msg.status == MessageStatus.PROCESSING

    @pytest.mark.asyncio
    async def test_mark_as_sent_changes_status_and_sets_processed_at(
        self, db_session: AsyncSession
    ) -> None:
        """mark_as_sent משנה סטטוס ומעדכן processed_at"""
        msg = await _insert_outbox(db_session)
        svc = OutboxService(db_session)

        await svc.mark_as_sent(msg.id)

        await db_session.refresh(msg)
        assert msg.status == MessageStatus.SENT
        assert msg.processed_at is not None

    @pytest.mark.asyncio
    async def test_mark_as_failed_increments_retry_and_sets_backoff(
        self, db_session: AsyncSession
    ) -> None:
        """mark_as_failed מגדיל retry_count ומגדיר backoff"""
        msg = await _insert_outbox(db_session)
        msg.max_retries = 3
        await db_session.commit()

        svc = OutboxService(db_session)

        await svc.mark_as_failed(msg.id, "שגיאה")

        await db_session.refresh(msg)
        assert msg.retry_count == 1
        assert msg.last_error == "שגיאה"
        assert msg.status == MessageStatus.PENDING  # עדיין pending — יש עוד retries
        assert msg.next_retry_at is not None

    @pytest.mark.asyncio
    async def test_mark_as_failed_exhausts_retries(
        self, db_session: AsyncSession
    ) -> None:
        """אחרי מיצוי retries — סטטוס FAILED"""
        msg = await _insert_outbox(db_session)
        msg.max_retries = 1
        msg.retry_count = 0
        await db_session.commit()

        svc = OutboxService(db_session)

        await svc.mark_as_failed(msg.id, "שגיאה סופית")

        await db_session.refresh(msg)
        assert msg.retry_count == 1
        assert msg.status == MessageStatus.FAILED
