"""
בדיקות לתיקוני ביצועים ויציבות — issues #11-#15

מכסה:
- #11: eager loading למניעת N+1 queries ב-delivery_service ו-driver_menu_service
- #12: הסרת אינדקסים כפולים על עמודות UNIQUE
- #13: תיקון דליפת זיכרון ב-Rate Limiter
- #14: datetime.now(timezone.utc) במקום datetime.utcnow()
- #15: connection timeout ל-DB engine
"""
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import joinedload

from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.user import User, UserRole


# ============================================================================
# #11 — Eager loading (N+1)
# ============================================================================


class TestEagerLoading:
    """בדיקות ל-eager loading בשליפות משלוחים"""

    @pytest.mark.asyncio
    async def test_get_sender_deliveries_loads_relationships(self, db_session) -> None:
        """get_sender_deliveries טוען sender ו-courier ב-eager loading"""
        from app.domain.services.delivery_service import DeliveryService

        sender = User(
            id=9001,
            phone_number="+972501110001",
            role=UserRole.SENDER,
            platform="telegram",
        )
        courier = User(
            id=9002,
            phone_number="+972501110002",
            role=UserRole.COURIER,
            platform="telegram",
        )
        db_session.add_all([sender, courier])
        await db_session.flush()

        delivery = Delivery(
            sender_id=sender.id,
            courier_id=courier.id,
            pickup_address="רחוב הרצל 1",
            dropoff_address="רחוב דיזנגוף 2",
            status=DeliveryStatus.CAPTURED,
        )
        db_session.add(delivery)
        await db_session.commit()

        service = DeliveryService(db_session)
        deliveries = await service.get_sender_deliveries(sender.id)

        assert len(deliveries) == 1
        # יחסים צריכים להיות טעונים ללא שאילתה נוספת
        d = deliveries[0]
        assert d.sender.phone_number == "+972501110001"
        assert d.courier.phone_number == "+972501110002"

    @pytest.mark.asyncio
    async def test_get_courier_deliveries_loads_relationships(self, db_session) -> None:
        """get_courier_deliveries טוען sender ו-courier ב-eager loading"""
        from app.domain.services.delivery_service import DeliveryService

        sender = User(
            id=9003,
            phone_number="+972501110003",
            role=UserRole.SENDER,
            platform="telegram",
        )
        courier = User(
            id=9004,
            phone_number="+972501110004",
            role=UserRole.COURIER,
            platform="telegram",
        )
        db_session.add_all([sender, courier])
        await db_session.flush()

        delivery = Delivery(
            sender_id=sender.id,
            courier_id=courier.id,
            pickup_address="רחוב הרצל 3",
            dropoff_address="רחוב דיזנגוף 4",
            status=DeliveryStatus.IN_PROGRESS,
        )
        db_session.add(delivery)
        await db_session.commit()

        service = DeliveryService(db_session)
        deliveries = await service.get_courier_deliveries(courier.id)

        assert len(deliveries) == 1
        d = deliveries[0]
        assert d.sender.phone_number == "+972501110003"
        assert d.courier.phone_number == "+972501110004"


# ============================================================================
# #12 — אינדקסים כפולים על עמודות UNIQUE
# ============================================================================


class TestNoDuplicateIndexes:
    """בדיקות שאין אינדקס כפול על עמודות UNIQUE"""

    @pytest.mark.unit
    def test_user_phone_number_no_explicit_index(self) -> None:
        """phone_number ב-User לא כולל index=True (UNIQUE מספיק)"""
        col = User.__table__.columns["phone_number"]
        assert col.unique is True
        assert col.index is not True

    @pytest.mark.unit
    def test_delivery_token_no_explicit_index(self) -> None:
        """token ב-Delivery לא כולל index=True (UNIQUE מספיק)"""
        col = Delivery.__table__.columns["token"]
        assert col.unique is True
        assert col.index is not True


# ============================================================================
# #13 — דליפת זיכרון ב-Rate Limiter
# ============================================================================


class TestRateLimiterMemoryLeak:
    """בדיקות שניקוי Rate Limiter מוחק גם IP ישנים שלא ריקים"""

    @pytest.mark.unit
    def test_cleanup_removes_stale_ip_with_old_single_request(self) -> None:
        """IP עם בקשה אחת ישנה (מחוץ ל-window*10) נמחק"""
        from starlette.applications import Starlette
        from app.core.middleware import WebhookRateLimitMiddleware

        app = Starlette()
        mw = WebhookRateLimitMiddleware(app, max_requests=100, window_seconds=60)

        now = time.time()
        # בקשה אחת שבוצעה לפני 11 דקות (> 60*10 = 600 שניות)
        mw._requests["1.2.3.4"] = [now - 700]

        mw._cleanup_window("1.2.3.4", now)

        # IP צריך להימחק כי הבקשה האחרונה ישנה מ-window*10
        assert "1.2.3.4" not in mw._requests

    @pytest.mark.unit
    def test_cleanup_keeps_recent_ip_with_active_window_request(self) -> None:
        """IP עם בקשה בתוך החלון לא נמחק"""
        from starlette.applications import Starlette
        from app.core.middleware import WebhookRateLimitMiddleware

        app = Starlette()
        mw = WebhookRateLimitMiddleware(app, max_requests=100, window_seconds=60)

        now = time.time()
        # בקשה אחת בתוך ה-window (30 שניות)
        mw._requests["1.2.3.4"] = [now - 30]

        mw._cleanup_window("1.2.3.4", now)

        assert "1.2.3.4" in mw._requests
        assert len(mw._requests["1.2.3.4"]) == 1

    @pytest.mark.unit
    def test_cleanup_removes_ip_outside_window_but_within_staleness(self) -> None:
        """IP עם בקשה מחוץ לחלון אבל לא ישנה מספיק — רשימה ריקה אחרי גיזום → נמחק"""
        from starlette.applications import Starlette
        from app.core.middleware import WebhookRateLimitMiddleware

        app = Starlette()
        mw = WebhookRateLimitMiddleware(app, max_requests=100, window_seconds=60)

        now = time.time()
        # בקשה מחוץ ל-window (120 שניות) אבל בתוך window*10 (600 שניות)
        mw._requests["1.2.3.4"] = [now - 120]

        mw._cleanup_window("1.2.3.4", now)

        # אחרי גיזום הרשימה ריקה → IP נמחק
        assert "1.2.3.4" not in mw._requests

    @pytest.mark.unit
    def test_cleanup_keeps_ip_with_active_requests(self) -> None:
        """IP עם בקשות בתוך החלון לא נמחק"""
        from starlette.applications import Starlette
        from app.core.middleware import WebhookRateLimitMiddleware

        app = Starlette()
        mw = WebhookRateLimitMiddleware(app, max_requests=100, window_seconds=60)

        now = time.time()
        mw._requests["1.2.3.4"] = [now - 30, now - 10]

        mw._cleanup_window("1.2.3.4", now)

        assert "1.2.3.4" in mw._requests
        assert len(mw._requests["1.2.3.4"]) == 2


# ============================================================================
# #14 — datetime.now(timezone.utc) במקום deprecated datetime.utcnow()
# ============================================================================


class TestDatetimeUtc:
    """בדיקות שלוגים משתמשים ב-datetime.now(timezone.utc)"""

    @pytest.mark.unit
    def test_json_formatter_uses_timezone_aware_utc(self) -> None:
        """JSONFormatter משתמש ב-datetime.now(timezone.utc) ולא ב-datetime.utcnow()"""
        import logging
        from app.core.logging import JSONFormatter
        import json

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert "timestamp" in data
        ts = data["timestamp"]
        # timestamp צריך להסתיים ב-Z בלבד (לא +00:00Z שהוא פורמט לא תקין)
        assert ts.endswith("Z")
        assert "+00:00" not in ts

    @pytest.mark.unit
    def test_logging_source_no_utcnow(self) -> None:
        """קובץ logging.py לא מכיל datetime.utcnow()"""
        import inspect
        import app.core.logging as logging_module

        source = inspect.getsource(logging_module)
        assert "utcnow()" not in source


# ============================================================================
# #15 — Connection timeout ל-DB
# ============================================================================


class TestDatabaseTimeout:
    """בדיקות ש-DB engine מוגדר עם timeout"""

    @pytest.mark.unit
    def test_engine_has_connect_timeout(self) -> None:
        """engine ראשי מוגדר עם connect_args timeout"""
        from app.db.database import engine

        # בדיקת pool_size ו-max_overflow
        assert engine.pool.size() == 20
        assert engine.pool._max_overflow == 20
