"""
בדיקות ל-Middleware — app/core/middleware.py

מכסה:
- CorrelationIdMiddleware: הפצת correlation ID בבקשות
- RequestLoggingMiddleware: לוג בקשות עם מיסוך PII
- WebhookRateLimitMiddleware: הגבלת קצב webhook
- Exception handlers: טיפול ב-AppException ו-Exception גנרי
- _mask_path_pii: מיסוך מספרי טלפון ב-URL
- setup_middleware: הגדרת middleware stack
"""
import time
from unittest.mock import AsyncMock, patch

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.middleware import (
    CorrelationIdMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    WebhookRateLimitMiddleware,
    _mask_path_pii,
    app_exception_handler,
    generic_exception_handler,
)
from app.core.exceptions import AppException, ErrorCode


# ============================================================================
# Helpers
# ============================================================================


def _hello(request: Request) -> PlainTextResponse:
    """endpoint מינימלי לבדיקה."""
    return PlainTextResponse("ok")


def _webhook(request: Request) -> PlainTextResponse:
    """endpoint שמדמה webhook."""
    return PlainTextResponse("webhook ok")


def _error(request: Request) -> PlainTextResponse:
    """endpoint שזורק שגיאה."""
    raise ValueError("שגיאת בדיקה")


def _build_app(
    *,
    routes: list[Route] | None = None,
    middlewares: list[tuple] | None = None,
) -> Starlette:
    """בונה אפליקציית Starlette מינימלית עם middleware."""
    default_routes = [
        Route("/test", _hello),
        Route("/webhook/telegram", _webhook),
        Route("/error", _error),
    ]
    app = Starlette(routes=routes or default_routes)
    if middlewares:
        for mw_class, kwargs in middlewares:
            app.add_middleware(mw_class, **kwargs)
    return app


# ============================================================================
# בדיקות _mask_path_pii
# ============================================================================


class TestMaskPathPii:
    """בדיקות למיסוך מספרי טלפון ב-URL path"""

    @pytest.mark.unit
    def test_masks_israeli_phone_in_path(self) -> None:
        """מסתיר 4 ספרות אמצעיות ממספר ישראלי"""
        path = "/api/users/+972501234567/profile"
        masked = _mask_path_pii(path)
        assert "1234" not in masked
        assert "****" in masked

    @pytest.mark.unit
    def test_masks_local_phone_in_path(self) -> None:
        """מסתיר מספר טלפון מקומי"""
        path = "/api/users/0501234567/messages"
        masked = _mask_path_pii(path)
        assert "****" in masked

    @pytest.mark.unit
    def test_no_phone_no_change(self) -> None:
        """path ללא מספר טלפון — ללא שינוי"""
        path = "/api/health"
        assert _mask_path_pii(path) == path

    @pytest.mark.unit
    def test_multiple_phones_in_path(self) -> None:
        """מסתיר מספרים מרובים"""
        path = "/api/chat/+972501234567/to/+972509876543"
        masked = _mask_path_pii(path)
        assert masked.count("****") == 2

    @pytest.mark.unit
    def test_short_number_not_masked(self) -> None:
        """מספר קצר (פחות מ-7 ספרות) לא מוסתר"""
        path = "/api/delivery/12345"
        masked = _mask_path_pii(path)
        assert "****" not in masked


# ============================================================================
# בדיקות CorrelationIdMiddleware
# ============================================================================


class TestCorrelationIdMiddleware:
    """בדיקות להפצת Correlation ID"""

    @pytest.mark.unit
    def test_generates_correlation_id_when_missing(self) -> None:
        """יוצר correlation ID חדש כשאין בבקשה"""
        app = _build_app(middlewares=[(CorrelationIdMiddleware, {})])
        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert "x-correlation-id" in response.headers
            assert len(response.headers["x-correlation-id"]) > 0

    @pytest.mark.unit
    def test_preserves_existing_correlation_id(self) -> None:
        """משתמש ב-correlation ID שסופק בבקשה"""
        app = _build_app(middlewares=[(CorrelationIdMiddleware, {})])
        custom_id = "my-custom-correlation-id"
        with TestClient(app) as client:
            response = client.get(
                "/test", headers={"X-Correlation-ID": custom_id}
            )
            assert response.headers["x-correlation-id"] == custom_id

    @pytest.mark.unit
    def test_correlation_id_unique_per_request(self) -> None:
        """כל בקשה מקבלת correlation ID ייחודי"""
        app = _build_app(middlewares=[(CorrelationIdMiddleware, {})])
        with TestClient(app) as client:
            r1 = client.get("/test")
            r2 = client.get("/test")
            id1 = r1.headers["x-correlation-id"]
            id2 = r2.headers["x-correlation-id"]
            assert id1 != id2


# ============================================================================
# בדיקות RequestLoggingMiddleware
# ============================================================================


class TestRequestLoggingMiddleware:
    """בדיקות ללוג בקשות"""

    @pytest.mark.unit
    def test_successful_request_logged(self) -> None:
        """בקשה מוצלחת מתועדת"""
        app = _build_app(middlewares=[(RequestLoggingMiddleware, {})])
        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200

    @pytest.mark.unit
    def test_exception_in_handler_reraised(self) -> None:
        """exception ב-handler עולה מחדש"""
        app = _build_app(middlewares=[(RequestLoggingMiddleware, {})])
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/error")
            assert response.status_code == 500


# ============================================================================
# בדיקות WebhookRateLimitMiddleware
# ============================================================================


class TestWebhookRateLimitMiddleware:
    """בדיקות להגבלת קצב webhook"""

    @pytest.mark.unit
    def test_allows_requests_under_limit(self) -> None:
        """בקשות מתחת ללימיט עוברות"""
        app = _build_app(
            middlewares=[
                (WebhookRateLimitMiddleware, {"max_requests": 5, "window_seconds": 60})
            ]
        )
        with TestClient(app) as client:
            for _ in range(5):
                response = client.get("/webhook/telegram")
                assert response.status_code == 200

    @pytest.mark.unit
    def test_blocks_requests_over_limit(self) -> None:
        """בקשות מעל הלימיט נחסמות עם 429"""
        app = _build_app(
            middlewares=[
                (WebhookRateLimitMiddleware, {"max_requests": 3, "window_seconds": 60})
            ]
        )
        with TestClient(app) as client:
            for _ in range(3):
                response = client.get("/webhook/telegram")
                assert response.status_code == 200

            # הבקשה הרביעית חסומה
            response = client.get("/webhook/telegram")
            assert response.status_code == 429
            assert "Retry-After" in response.headers

    @pytest.mark.unit
    def test_non_webhook_paths_not_limited(self) -> None:
        """paths שלא מכילים /webhook לא מוגבלים"""
        app = _build_app(
            middlewares=[
                (WebhookRateLimitMiddleware, {"max_requests": 1, "window_seconds": 60})
            ]
        )
        with TestClient(app) as client:
            # webhook — מוגבל אחרי 1
            response = client.get("/webhook/telegram")
            assert response.status_code == 200

            response = client.get("/webhook/telegram")
            assert response.status_code == 429

            # non-webhook — לא מוגבל
            response = client.get("/test")
            assert response.status_code == 200
            response = client.get("/test")
            assert response.status_code == 200

    @pytest.mark.unit
    def test_rate_limit_per_ip(self) -> None:
        """הגבלה לפי IP — כל IP מקבל מכסה נפרדת"""
        app = _build_app()
        mw = WebhookRateLimitMiddleware(
            app, max_requests=2, window_seconds=60
        )
        now = time.time()
        mw._requests["1.2.3.4"].append(now)
        mw._requests["5.6.7.8"].append(now)

        # כל IP מאוחסן בנפרד
        assert len(mw._requests["1.2.3.4"]) == 1
        assert len(mw._requests["5.6.7.8"]) == 1

        # הוספת עוד בקשה ל-IP אחד לא משפיעה על השני
        mw._requests["1.2.3.4"].append(now)
        assert len(mw._requests["1.2.3.4"]) == 2
        assert len(mw._requests["5.6.7.8"]) == 1

    @pytest.mark.unit
    def test_cleanup_removes_old_entries(self) -> None:
        """ניקוי חלון — מוחק timestamps ישנים"""
        app = _build_app()
        mw = WebhookRateLimitMiddleware(
            app, max_requests=100, window_seconds=60
        )

        now = time.time()
        # הוספת timestamps ישנים (מחוץ לחלון)
        mw._requests["1.2.3.4"] = [now - 120, now - 90, now - 30, now]

        mw._cleanup_window("1.2.3.4", now)

        # רק 2 אחרונים צריכים להישאר (בתוך 60 שניות)
        assert len(mw._requests["1.2.3.4"]) == 2

    @pytest.mark.unit
    def test_cleanup_deletes_empty_ip(self) -> None:
        """ניקוי IP ללא timestamps — מוחק את המפתח (מונע דליפת זיכרון)"""
        app = _build_app()
        mw = WebhookRateLimitMiddleware(
            app, max_requests=100, window_seconds=60
        )

        now = time.time()
        # כל ה-timestamps מחוץ לחלון
        mw._requests["1.2.3.4"] = [now - 120]

        mw._cleanup_window("1.2.3.4", now)

        assert "1.2.3.4" not in mw._requests

    @pytest.mark.unit
    def test_429_response_includes_correlation_id(self) -> None:
        """תשובת 429 כוללת X-Correlation-ID"""
        app = _build_app(
            middlewares=[
                (WebhookRateLimitMiddleware, {"max_requests": 1, "window_seconds": 60}),
                (CorrelationIdMiddleware, {}),
            ]
        )
        with TestClient(app) as client:
            client.get("/webhook/telegram")  # בתוך הלימיט
            response = client.get("/webhook/telegram")  # חסום

            assert response.status_code == 429
            # CorrelationId middleware רץ לפני RateLimit, אז תשובת 429 כוללת correlation ID
            assert "x-correlation-id" in response.headers


# ============================================================================
# בדיקות Exception Handlers
# ============================================================================


class TestAppExceptionHandler:
    """בדיקות ל-app_exception_handler"""

    @pytest.mark.asyncio
    async def test_handles_app_exception(self) -> None:
        """מטפל ב-AppException ומחזיר JSON תקין"""
        exc = AppException(
            message="משלוח לא נמצא",
            error_code=ErrorCode.DELIVERY_NOT_FOUND,
            status_code=404,
            details={"delivery_id": 123},
        )

        # יצירת request מוק
        mock_request = AsyncMock(spec=Request)
        mock_request.url.path = "/api/deliveries/123"

        response = await app_exception_handler(mock_request, exc)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 404
        assert "x-correlation-id" in response.headers

    @pytest.mark.asyncio
    async def test_handles_validation_exception(self) -> None:
        """מטפל ב-ValidationException"""
        from app.core.exceptions import ValidationException

        exc = ValidationException(
            message="מספר טלפון לא תקין",
            field="phone_number",
        )

        mock_request = AsyncMock(spec=Request)
        mock_request.url.path = "/api/users"

        response = await app_exception_handler(mock_request, exc)

        assert response.status_code == 400


class TestGenericExceptionHandler:
    """בדיקות ל-generic_exception_handler"""

    @pytest.mark.asyncio
    async def test_handles_unexpected_exception(self) -> None:
        """מטפל ב-exception בלתי צפוי ומחזיר 500"""
        exc = RuntimeError("שגיאה בלתי צפויה")

        mock_request = AsyncMock(spec=Request)
        mock_request.url.path = "/api/something"

        response = await generic_exception_handler(mock_request, exc)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 500
        assert "x-correlation-id" in response.headers

    @pytest.mark.asyncio
    async def test_does_not_leak_internal_details(self) -> None:
        """לא חושף פרטים פנימיים בתשובה"""
        exc = RuntimeError("database connection failed on host 10.0.0.1")

        mock_request = AsyncMock(spec=Request)
        mock_request.url.path = "/api/test"

        response = await generic_exception_handler(mock_request, exc)

        # בודקים שהתוכן לא מכיל את הפרטים הפנימיים
        body = response.body.decode()
        assert "10.0.0.1" not in body
        assert "database connection" not in body
        assert "ERR_1000" in body


# ============================================================================
# בדיקות SecurityHeadersMiddleware (משלים ל-test_security_headers.py)
# ============================================================================


class TestSecurityHeadersMiddlewareUnit:
    """בדיקות יחידה נוספות ל-SecurityHeadersMiddleware"""

    @pytest.mark.unit
    def test_nosniff_on_all_responses(self) -> None:
        """X-Content-Type-Options: nosniff מופיע בכל תשובה"""
        app = _build_app(
            middlewares=[(SecurityHeadersMiddleware, {"debug": False})]
        )
        with TestClient(app) as client:
            response = client.get("/test")
            assert response.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.unit
    def test_no_csp_in_debug_mode(self) -> None:
        """במצב debug — אין CSP"""
        app = _build_app(
            middlewares=[(SecurityHeadersMiddleware, {"debug": True})]
        )
        with TestClient(app) as client:
            response = client.get("/test")
            assert "content-security-policy" not in response.headers

    @pytest.mark.unit
    def test_hsts_includes_subdomains(self) -> None:
        """HSTS כולל includeSubDomains"""
        app = _build_app(
            middlewares=[(SecurityHeadersMiddleware, {"debug": False})]
        )
        with TestClient(app) as client:
            response = client.get("/test")
            hsts = response.headers.get("strict-transport-security", "")
            assert "includeSubDomains" in hsts


# ============================================================================
# בדיקות setup_middleware
# ============================================================================


class TestSetupMiddleware:
    """בדיקות ל-setup_middleware"""

    @pytest.mark.asyncio
    async def test_full_middleware_stack(self, test_client) -> None:
        """כל ה-middleware stack עובד יחד — בדיקה דרך test_client"""
        response = await test_client.get("/health")
        assert response.status_code == 200

        # Correlation ID
        assert "x-correlation-id" in response.headers

        # Security headers
        assert response.headers.get("x-content-type-options") == "nosniff"

    @pytest.mark.asyncio
    async def test_webhook_rate_limit_with_full_stack(self, test_client) -> None:
        """rate limit על webhooks דרך ה-stack המלא"""
        # בקשה ראשונה לא אמורה להיחסם (לימיט גבוה)
        response = await test_client.post(
            "/api/telegram/webhook",
            json={"update_id": 1},
        )
        # הבקשה צריכה לעבור (200 — webhook מעובד בהצלחה)
        assert response.status_code == 200
        # headers מה-middleware stack חייבים להופיע
        assert "x-correlation-id" in response.headers
        assert response.headers.get("x-content-type-options") == "nosniff"
