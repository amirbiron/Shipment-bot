"""
בדיקות יחידה ל-Admin Debug Endpoints.

בדיקות עבור:
1. אימות API key (אבטחה)
2. סטטוס circuit breakers
3. שאילתת הודעות outbox + retry ידני
4. בדיקת מצב state machine של משתמש + force-state
"""
import pytest
from unittest.mock import patch

import httpx

from app.core.config import settings
from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from app.db.models.outbox_message import OutboxMessage, MessagePlatform, MessageStatus
from app.db.models.conversation_session import ConversationSession
from app.db.models.user import UserRole

_TEST_API_KEY = "test-admin-api-key-for-tests"
_ADMIN_HEADERS = {"X-Admin-API-Key": _TEST_API_KEY}


@pytest.fixture(autouse=True)
def set_admin_api_key():
    """מגדיר ADMIN_API_KEY לבדיקות"""
    with patch.object(settings, "ADMIN_API_KEY", _TEST_API_KEY):
        yield


# ============================================================================
# אימות API Key
# ============================================================================


class TestAdminAuth:
    """בדיקות אבטחה — API key נדרש לכל endpoint."""

    @pytest.mark.unit
    async def test_no_api_key_returns_401(self, test_client: httpx.AsyncClient) -> None:
        """בקשה ללא header של API key מחזירה 401."""
        response = await test_client.get("/api/admin/debug/circuit-breakers")
        assert response.status_code == 401

    @pytest.mark.unit
    async def test_wrong_api_key_returns_403(self, test_client: httpx.AsyncClient) -> None:
        """API key שגוי מחזיר 403."""
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers={"X-Admin-API-Key": "wrong-key"},
        )
        assert response.status_code == 403

    @pytest.mark.unit
    async def test_empty_admin_api_key_setting_returns_403(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כש-ADMIN_API_KEY ריק בסביבה — הגישה חסומה לחלוטין."""
        with patch.object(settings, "ADMIN_API_KEY", ""):
            response = await test_client.get(
                "/api/admin/debug/circuit-breakers",
                headers={"X-Admin-API-Key": "any-key"},
            )
        assert response.status_code == 403

    @pytest.mark.unit
    async def test_valid_api_key_returns_200(self, test_client: httpx.AsyncClient) -> None:
        """API key תקין מאפשר גישה."""
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200


# ============================================================================
# Circuit Breakers
# ============================================================================


class TestCircuitBreakersEndpoint:
    """בדיקות ל-GET /api/admin/debug/circuit-breakers."""

    @pytest.mark.unit
    async def test_returns_all_breakers(self, test_client: httpx.AsyncClient) -> None:
        """מחזיר סטטוס של כל 3 circuit breakers."""
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        services = {cb["service"] for cb in data}
        assert services == {"telegram", "whatsapp", "whatsapp_admin"}

    @pytest.mark.unit
    async def test_closed_breaker_status(self, test_client: httpx.AsyncClient) -> None:
        """circuit breaker סגור מחזיר state=closed."""
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers=_ADMIN_HEADERS,
        )
        data = response.json()
        telegram_cb = next(cb for cb in data if cb["service"] == "telegram")
        assert telegram_cb["state"] == "closed"
        assert telegram_cb["failure_count"] == 0
        assert telegram_cb["retry_after_seconds"] == 0.0

    @pytest.mark.unit
    async def test_open_breaker_status(self, test_client: httpx.AsyncClient) -> None:
        """circuit breaker פתוח מחזיר state=open עם retry_after."""
        cb = CircuitBreaker.get_instance(
            "telegram",
            CircuitBreakerConfig(failure_threshold=2, timeout_seconds=60.0),
        )
        # גורמים לפתיחה ע"י כשלונות
        for _ in range(2):
            await cb.record_failure(Exception("test"))

        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers=_ADMIN_HEADERS,
        )
        data = response.json()
        telegram_cb = next(cb for cb in data if cb["service"] == "telegram")
        assert telegram_cb["state"] == "open"
        assert telegram_cb["failure_count"] >= 2
        assert telegram_cb["retry_after_seconds"] > 0


# ============================================================================
# Outbox Summary
# ============================================================================


class TestOutboxSummary:
    """בדיקות ל-GET /api/admin/debug/outbox/summary."""

    @pytest.mark.unit
    async def test_empty_outbox_summary(self, test_client: httpx.AsyncClient) -> None:
        """outbox ריק מחזיר אפסים בכל הסטטוסים."""
        response = await test_client.get(
            "/api/admin/debug/outbox/summary",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["pending"] == 0
        assert data["failed"] == 0

    @pytest.mark.unit
    async def test_outbox_summary_with_messages(
        self, test_client: httpx.AsyncClient, db_session
    ) -> None:
        """סיכום עם הודעות בסטטוסים שונים."""
        # יצירת הודעות בסטטוסים שונים
        for i in range(3):
            db_session.add(OutboxMessage(
                platform=MessagePlatform.TELEGRAM,
                recipient_id="123456",
                message_type="test",
                message_content={"text": f"pending_{i}"},
                status=MessageStatus.PENDING,
            ))
        db_session.add(OutboxMessage(
            platform=MessagePlatform.WHATSAPP,
            recipient_id="972501234567",
            message_type="test",
            message_content={"text": "failed"},
            status=MessageStatus.FAILED,
            last_error="timeout",
        ))
        await db_session.commit()

        response = await test_client.get(
            "/api/admin/debug/outbox/summary",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pending"] == 3
        assert data["failed"] == 1
        assert data["total"] == 4


# ============================================================================
# Outbox Messages
# ============================================================================


class TestOutboxMessages:
    """בדיקות ל-GET /api/admin/debug/outbox/messages."""

    @pytest.mark.unit
    async def test_get_failed_messages_default(
        self, test_client: httpx.AsyncClient, db_session
    ) -> None:
        """ברירת מחדל — מחזיר רק הודעות כושלות."""
        db_session.add(OutboxMessage(
            platform=MessagePlatform.TELEGRAM,
            recipient_id="123",
            message_type="test",
            message_content={"text": "ok"},
            status=MessageStatus.SENT,
        ))
        db_session.add(OutboxMessage(
            platform=MessagePlatform.TELEGRAM,
            recipient_id="456",
            message_type="test",
            message_content={"text": "fail"},
            status=MessageStatus.FAILED,
            last_error="circuit breaker open",
            retry_count=3,
            max_retries=3,
        ))
        await db_session.commit()

        response = await test_client.get(
            "/api/admin/debug/outbox/messages",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "failed"
        assert data[0]["last_error"] == "circuit breaker open"

    @pytest.mark.unit
    async def test_get_pending_messages(
        self, test_client: httpx.AsyncClient, db_session
    ) -> None:
        """סינון לפי סטטוס pending."""
        db_session.add(OutboxMessage(
            platform=MessagePlatform.TELEGRAM,
            recipient_id="123",
            message_type="test",
            message_content={"text": "pending"},
            status=MessageStatus.PENDING,
        ))
        db_session.add(OutboxMessage(
            platform=MessagePlatform.TELEGRAM,
            recipient_id="456",
            message_type="test",
            message_content={"text": "failed"},
            status=MessageStatus.FAILED,
        ))
        await db_session.commit()

        response = await test_client.get(
            "/api/admin/debug/outbox/messages",
            params={"message_status": "pending"},
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "pending"

    @pytest.mark.unit
    async def test_invalid_status_returns_400(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """סטטוס לא תקין מחזיר 400."""
        response = await test_client.get(
            "/api/admin/debug/outbox/messages",
            params={"message_status": "invalid_status"},
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 400


# ============================================================================
# Outbox Retry
# ============================================================================


class TestOutboxRetry:
    """בדיקות ל-POST /api/admin/debug/outbox/messages/{id}/retry."""

    @pytest.mark.unit
    async def test_retry_failed_message(
        self, test_client: httpx.AsyncClient, db_session
    ) -> None:
        """retry מוצלח — מעביר מ-failed ל-pending."""
        msg = OutboxMessage(
            platform=MessagePlatform.TELEGRAM,
            recipient_id="123",
            message_type="test",
            message_content={"text": "retry me"},
            status=MessageStatus.FAILED,
            last_error="timeout",
            retry_count=3,
            max_retries=3,
        )
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(msg)

        response = await test_client.post(
            f"/api/admin/debug/outbox/messages/{msg.id}/retry",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["previous_status"] == "failed"
        assert data["new_status"] == "pending"

    @pytest.mark.unit
    async def test_retry_non_failed_returns_400(
        self, test_client: httpx.AsyncClient, db_session
    ) -> None:
        """retry על הודעה שלא בסטטוס failed מחזיר 400."""
        msg = OutboxMessage(
            platform=MessagePlatform.TELEGRAM,
            recipient_id="123",
            message_type="test",
            message_content={"text": "pending"},
            status=MessageStatus.PENDING,
        )
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(msg)

        response = await test_client.post(
            f"/api/admin/debug/outbox/messages/{msg.id}/retry",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 400

    @pytest.mark.unit
    async def test_retry_nonexistent_returns_404(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """retry על הודעה שלא קיימת מחזיר 404."""
        response = await test_client.post(
            "/api/admin/debug/outbox/messages/99999/retry",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 404


# ============================================================================
# User State
# ============================================================================


class TestUserState:
    """בדיקות ל-GET /api/admin/debug/users/{user_id}/state."""

    @pytest.mark.unit
    async def test_get_user_state(
        self, test_client: httpx.AsyncClient, db_session, user_factory
    ) -> None:
        """שליפת מצב state machine של משתמש."""
        user = await user_factory(
            phone_number="+972509999999",
            name="דיבוג משתמש",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="999999",
        )
        session = ConversationSession(
            user_id=user.id,
            platform="telegram",
            current_state="SENDER.DELIVERY.PICKUP_CITY",
            context_data={"pickup_city": "תל אביב"},
        )
        db_session.add(session)
        await db_session.commit()

        response = await test_client.get(
            f"/api/admin/debug/users/{user.id}/state",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == user.id
        assert data["current_state"] == "SENDER.DELIVERY.PICKUP_CITY"
        assert data["context_data"]["pickup_city"] == "תל אביב"
        assert data["user_role"] == "sender"

    @pytest.mark.unit
    async def test_get_user_state_with_platform(
        self, test_client: httpx.AsyncClient, db_session, user_factory
    ) -> None:
        """סינון state machine לפי פלטפורמה."""
        user = await user_factory(
            phone_number="+972509999998",
            name="דו-פלטפורמי",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="999998",
        )
        # שני sessions — telegram + whatsapp
        db_session.add(ConversationSession(
            user_id=user.id, platform="telegram",
            current_state="COURIER.MENU", context_data={},
        ))
        db_session.add(ConversationSession(
            user_id=user.id, platform="whatsapp",
            current_state="COURIER.VIEW_AVAILABLE", context_data={"page": 1},
        ))
        await db_session.commit()

        response = await test_client.get(
            f"/api/admin/debug/users/{user.id}/state",
            params={"platform": "whatsapp"},
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["platform"] == "whatsapp"
        assert data["current_state"] == "COURIER.VIEW_AVAILABLE"

    @pytest.mark.unit
    async def test_multi_platform_user_without_filter_returns_latest(
        self, test_client: httpx.AsyncClient, db_session, user_factory
    ) -> None:
        """משתמש עם sessions בשתי פלטפורמות — בלי סינון מחזיר את האחרון (לא קורס)."""
        user = await user_factory(
            phone_number="+972509999990",
            name="דו-פלטפורמי ללא סינון",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="999990",
        )
        db_session.add(ConversationSession(
            user_id=user.id, platform="telegram",
            current_state="COURIER.MENU", context_data={},
        ))
        db_session.add(ConversationSession(
            user_id=user.id, platform="whatsapp",
            current_state="COURIER.VIEW_WALLET", context_data={"page": 2},
        ))
        await db_session.commit()

        # ללא platform — לא צריך לקרוס עם MultipleResultsFound
        response = await test_client.get(
            f"/api/admin/debug/users/{user.id}/state",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == user.id
        # מחזיר session כלשהו — העיקר שלא קרס
        assert data["current_state"] in ("COURIER.MENU", "COURIER.VIEW_WALLET")

    @pytest.mark.unit
    async def test_user_not_found_returns_404(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """משתמש שלא קיים מחזיר 404."""
        response = await test_client.get(
            "/api/admin/debug/users/99999/state",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 404

    @pytest.mark.unit
    async def test_session_not_found_returns_404(
        self, test_client: httpx.AsyncClient, user_factory
    ) -> None:
        """משתמש בלי session מחזיר 404."""
        user = await user_factory(
            phone_number="+972509999997",
            name="ללא session",
            role=UserRole.SENDER,
        )
        response = await test_client.get(
            f"/api/admin/debug/users/{user.id}/state",
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 404


# ============================================================================
# Force State
# ============================================================================


class TestForceState:
    """בדיקות ל-POST /api/admin/debug/users/{user_id}/force-state."""

    @pytest.mark.unit
    async def test_force_state_success(
        self, test_client: httpx.AsyncClient, db_session, user_factory
    ) -> None:
        """איפוס כפוי של state machine."""
        user = await user_factory(
            phone_number="+972509999996",
            name="תקוע",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id="999996",
        )
        session = ConversationSession(
            user_id=user.id,
            platform="telegram",
            current_state="COURIER.REGISTER.COLLECT_SELFIE",
            context_data={"step": 3, "name": "בדיקה"},
        )
        db_session.add(session)
        await db_session.commit()

        response = await test_client.post(
            f"/api/admin/debug/users/{user.id}/force-state",
            json={
                "platform": "telegram",
                "new_state": "COURIER.MENU",
                "clear_context": True,
            },
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["current_state"] == "COURIER.MENU"
        assert data["context_data"] == {}

    @pytest.mark.unit
    async def test_force_state_keep_context(
        self, test_client: httpx.AsyncClient, db_session, user_factory
    ) -> None:
        """איפוס כפוי עם שמירת context."""
        user = await user_factory(
            phone_number="+972509999995",
            name="שמירת קונטקסט",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="999995",
        )
        session = ConversationSession(
            user_id=user.id,
            platform="telegram",
            current_state="SENDER.DELIVERY.DROPOFF_CITY",
            context_data={"pickup_city": "חיפה", "pickup_street": "הרצל"},
        )
        db_session.add(session)
        await db_session.commit()

        response = await test_client.post(
            f"/api/admin/debug/users/{user.id}/force-state",
            json={
                "platform": "telegram",
                "new_state": "SENDER.MENU",
                "clear_context": False,
            },
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["current_state"] == "SENDER.MENU"
        assert data["context_data"]["pickup_city"] == "חיפה"

    @pytest.mark.unit
    async def test_force_state_user_not_found(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """איפוס על משתמש שלא קיים מחזיר 404."""
        response = await test_client.post(
            "/api/admin/debug/users/99999/force-state",
            json={"platform": "telegram", "new_state": "SENDER.MENU"},
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 404

    @pytest.mark.unit
    async def test_force_state_session_not_found(
        self, test_client: httpx.AsyncClient, user_factory
    ) -> None:
        """איפוס על משתמש ללא session מחזיר 404."""
        user = await user_factory(
            phone_number="+972509999994",
            name="ללא session",
            role=UserRole.SENDER,
        )
        response = await test_client.post(
            f"/api/admin/debug/users/{user.id}/force-state",
            json={"platform": "telegram", "new_state": "SENDER.MENU"},
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 404

    @pytest.mark.unit
    async def test_force_state_invalid_platform(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """פלטפורמה לא תקינה מחזירה 422."""
        response = await test_client.post(
            "/api/admin/debug/users/1/force-state",
            json={"platform": "sms", "new_state": "SENDER.MENU"},
            headers=_ADMIN_HEADERS,
        )
        assert response.status_code == 422
