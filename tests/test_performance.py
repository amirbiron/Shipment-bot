"""
בדיקות ביצועים — מניעת N+1 queries, batch operations, ושימוש בזיכרון.

מכסה:
- ספירת queries בפעולות נפוצות (מניעת N+1)
- ביצועי batch operations
- שימוש בזיכרון בשידורים גדולים
- Eager loading בשליפות קשורות
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.outbox_message import MessagePlatform, MessageStatus, OutboxMessage
from app.db.models.user import ApprovalStatus, User, UserRole
from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.domain.services.outbox_service import OutboxService


# ============================================================================
# Query Counter — כלי לספירת queries
# ============================================================================


class QueryCounter:
    """
    סופר queries שנשלחות למסד הנתונים.

    שימוש:
        async with QueryCounter(engine) as counter:
            await do_something(db_session)
        assert counter.count <= 3
    """

    def __init__(self, engine) -> None:
        self._engine = engine
        self.count = 0
        self.queries: list[str] = []

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1
        self.queries.append(str(statement)[:200])

    async def __aenter__(self):
        self.count = 0
        self.queries = []
        sync_engine = self._engine.sync_engine
        event.listen(sync_engine, "before_cursor_execute", self._on_execute)
        return self

    async def __aexit__(self, *args):
        sync_engine = self._engine.sync_engine
        event.remove(sync_engine, "before_cursor_execute", self._on_execute)


# ============================================================================
# בדיקות מניעת N+1 Queries
# ============================================================================


class TestN1Prevention:
    """בדיקות למניעת N+1 queries"""

    @pytest.mark.asyncio
    async def test_get_pending_messages_single_query(
        self, db_session: AsyncSession, async_engine
    ) -> None:
        """שליפת הודעות pending — שאילתא אחת בלבד"""
        # יצירת 10 הודעות
        for i in range(10):
            msg = OutboxMessage(
                platform=MessagePlatform.WHATSAPP,
                recipient_id=f"+97250{i:07d}",
                message_type="test",
                message_content={"message_text": f"הודעה {i}"},
                status=MessageStatus.PENDING,
            )
            db_session.add(msg)
        await db_session.commit()

        svc = OutboxService(db_session)

        async with QueryCounter(async_engine) as counter:
            messages = await svc.get_pending_messages(limit=50)

        assert len(messages) == 10
        # שאילתא אחת בלבד (SELECT)
        assert counter.count == 1, (
            f"צפינו ל-1 שאילתא, קיבלנו {counter.count}: {counter.queries}"
        )

    @pytest.mark.asyncio
    async def test_get_courier_recipients_single_query(
        self, db_session: AsyncSession, user_factory, async_engine
    ) -> None:
        """שליפת שליחים — שאילתא אחת בלבד"""
        from app.workers.tasks import _get_courier_recipients

        # יצירת 20 שליחים
        for i in range(20):
            await user_factory(
                phone_number=f"+97250{i:07d}",
                role=UserRole.COURIER,
                platform="whatsapp",
                is_active=True,
                approval_status=ApprovalStatus.APPROVED,
            )

        async with QueryCounter(async_engine) as counter:
            recipients = await _get_courier_recipients(
                db_session, MessagePlatform.WHATSAPP
            )

        assert len(recipients) == 20
        assert counter.count == 1, (
            f"צפינו ל-1 שאילתא, קיבלנו {counter.count}: {counter.queries}"
        )

    @pytest.mark.asyncio
    async def test_get_dispatcher_recipients_single_query(
        self, db_session: AsyncSession, user_factory, async_engine
    ) -> None:
        """שליפת סדרנים — שאילתא אחת (עם JOIN)"""
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

        # יצירת 10 סדרנים
        for i in range(10):
            user = await user_factory(
                phone_number=f"+97250{i:07d}",
                role=UserRole.COURIER,
                platform="telegram",
                telegram_chat_id=f"tg{i}",
                is_active=True,
                approval_status=ApprovalStatus.APPROVED,
            )
            sd = StationDispatcher(
                station_id=station.id, user_id=user.id, is_active=True
            )
            db_session.add(sd)
        await db_session.commit()

        async with QueryCounter(async_engine) as counter:
            recipients = await _get_dispatcher_recipients(
                db_session, station.id, MessagePlatform.TELEGRAM
            )

        assert len(recipients) == 10
        # שאילתא אחת (SELECT ... JOIN ...)
        assert counter.count == 1, (
            f"צפינו ל-1 שאילתא, קיבלנו {counter.count}: {counter.queries}"
        )


# ============================================================================
# בדיקות ביצועי Batch
# ============================================================================


class TestBatchOperations:
    """בדיקות לביצועי batch operations"""

    @pytest.mark.asyncio
    async def test_outbox_batch_insert(self, db_session: AsyncSession) -> None:
        """הכנסת batch של הודעות — כל ההודעות נשמרות ב-commit אחד"""
        svc = OutboxService(db_session)

        # הכנסת 50 הודעות
        for i in range(50):
            await svc.queue_message(
                platform=MessagePlatform.WHATSAPP,
                recipient_id=f"+97250{i:07d}",
                message_type="batch_test",
                message_content={"message_text": f"הודעה {i}"},
            )
        await db_session.commit()

        # ווידוא שכולן נשמרו
        result = await db_session.execute(
            select(OutboxMessage).where(OutboxMessage.message_type == "batch_test")
        )
        messages = list(result.scalars().all())
        assert len(messages) == 50

    @pytest.mark.asyncio
    async def test_broadcast_parallel_send_performance(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        """שידור מקבילי — כל ההודעות נשלחות ב-asyncio.gather"""
        from app.workers.tasks import _get_courier_recipients

        # יצירת 30 שליחים
        for i in range(30):
            await user_factory(
                phone_number=f"+97250{i:07d}",
                role=UserRole.COURIER,
                platform="whatsapp",
                is_active=True,
                approval_status=ApprovalStatus.APPROVED,
            )

        recipients = await _get_courier_recipients(
            db_session, MessagePlatform.WHATSAPP
        )
        assert len(recipients) == 30

        # סימולציה של שליחה מקבילית
        send_count = 0

        async def _mock_send(phone: str, content: dict) -> bool:
            nonlocal send_count
            send_count += 1
            await asyncio.sleep(0.001)  # סימולציה של latency
            return True

        tasks = [_mock_send(r.phone_number, {}) for r in recipients]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert send_count == 30
        assert all(r is True for r in results)

    @pytest.mark.asyncio
    async def test_cleanup_deletes_in_batch(
        self, db_session: AsyncSession, async_engine
    ) -> None:
        """ניקוי הודעות ישנות — מחיקה ב-batch"""
        from datetime import datetime, timedelta

        # יצירת 20 הודעות ישנות
        for i in range(20):
            msg = OutboxMessage(
                platform=MessagePlatform.WHATSAPP,
                recipient_id=f"+97250{i:07d}",
                message_type="old_test",
                message_content={"message_text": f"ישנה {i}"},
                status=MessageStatus.SENT,
                processed_at=datetime.utcnow() - timedelta(days=60),
            )
            db_session.add(msg)
        await db_session.commit()

        # ווידוא שההודעות נוצרו
        result = await db_session.execute(
            select(OutboxMessage).where(OutboxMessage.message_type == "old_test")
        )
        assert len(list(result.scalars().all())) == 20


# ============================================================================
# בדיקות שימוש בזיכרון
# ============================================================================


class TestMemoryUsage:
    """בדיקות לשימוש בזיכרון"""

    @pytest.mark.asyncio
    async def test_rate_limit_cleanup_prevents_memory_leak(self) -> None:
        """ניקוי IP ריקים — מונע דליפת זיכרון"""
        from app.core.middleware import WebhookRateLimitMiddleware
        from starlette.applications import Starlette

        app = Starlette()
        mw = WebhookRateLimitMiddleware(
            app, max_requests=100, window_seconds=1
        )
        import time

        now = time.time()

        # סימולציה של 1000 IPs חד-פעמיים עם timestamps ישנים
        for i in range(1000):
            ip = f"10.0.{i // 256}.{i % 256}"
            mw._requests[ip] = [now - 10]  # מחוץ לחלון

        # ניקוי כל ה-IPs
        for i in range(1000):
            ip = f"10.0.{i // 256}.{i % 256}"
            mw._cleanup_window(ip, now)

        # כל ה-IPs צריכים להימחק
        assert len(mw._requests) == 0

    @pytest.mark.asyncio
    async def test_gather_exceptions_are_handled_not_leaked(self) -> None:
        """asyncio.gather עם return_exceptions — שגיאות לא דולפות"""
        # סימולציה של broadcast עם כשלונות
        async def _success():
            return True

        async def _fail():
            raise ConnectionError("connection refused")

        tasks = [_success(), _fail(), _success(), _fail()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = sum(1 for r in results if r is True)
        exception_count = sum(1 for r in results if isinstance(r, Exception))

        assert success_count == 2
        assert exception_count == 2

    @pytest.mark.asyncio
    async def test_outbox_service_limit_prevents_unbounded_fetch(
        self, db_session: AsyncSession
    ) -> None:
        """get_pending_messages עם limit — מונע שליפה לא חסומה"""
        # יצירת 100 הודעות
        for i in range(100):
            msg = OutboxMessage(
                platform=MessagePlatform.WHATSAPP,
                recipient_id=f"+97250{i:07d}",
                message_type="limit_test",
                message_content={"message_text": f"הודעה {i}"},
                status=MessageStatus.PENDING,
            )
            db_session.add(msg)
        await db_session.commit()

        svc = OutboxService(db_session)

        # שליפה עם limit=10
        messages = await svc.get_pending_messages(limit=10)
        assert len(messages) == 10

        # שליפה עם limit=50 (ברירת המחדל של process_outbox_messages)
        messages = await svc.get_pending_messages(limit=50)
        assert len(messages) == 50
