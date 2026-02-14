"""
בדיקות ל-SecurityHeadersMiddleware — כותרות אבטחה נגד mixed content.

בודק ש:
- בפרודקשן (DEBUG=False): כותרות CSP, HSTS ו-nosniff מוחזרות בכל תשובה.
- בפיתוח (DEBUG=True): CSP ו-HSTS לא מתווספות (לא לחסום HTTP מקומי),
  אבל nosniff מוחל תמיד (בטוח ולא חוסם פיתוח).
"""
import pytest

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.middleware import SecurityHeadersMiddleware


def _hello(request):
    """endpoint מינימלי לבדיקה."""
    return PlainTextResponse("ok")


def _build_test_app(*, debug: bool) -> Starlette:
    """בונה אפליקציית Starlette מינימלית עם SecurityHeadersMiddleware."""
    test_app = Starlette(routes=[Route("/test", _hello)])
    test_app.add_middleware(SecurityHeadersMiddleware, debug=debug)
    return test_app


class TestSecurityHeadersProduction:
    """כותרות אבטחה כש-DEBUG=False (ברירת מחדל בפרודקשן)."""

    @pytest.mark.asyncio
    async def test_csp_upgrade_insecure_requests(self, test_client) -> None:
        """Content-Security-Policy: upgrade-insecure-requests חייב להופיע."""
        response = await test_client.get("/health")
        assert response.status_code == 200
        csp = response.headers.get("content-security-policy", "")
        assert "upgrade-insecure-requests" in csp

    @pytest.mark.asyncio
    async def test_hsts_header(self, test_client) -> None:
        """Strict-Transport-Security חייב להופיע עם max-age."""
        response = await test_client.get("/health")
        hsts = response.headers.get("strict-transport-security", "")
        assert "max-age=" in hsts
        assert "includeSubDomains" in hsts

    @pytest.mark.asyncio
    async def test_x_content_type_options(self, test_client) -> None:
        """X-Content-Type-Options: nosniff חייב להופיע."""
        response = await test_client.get("/health")
        assert response.headers.get("x-content-type-options") == "nosniff"

    @pytest.mark.asyncio
    async def test_headers_on_api_endpoints(self, test_client) -> None:
        """כותרות אבטחה מופיעות גם ב-endpoints שמחזירים 401."""
        response = await test_client.get("/api/panel/dashboard")
        # גם בתשובת שגיאה (401 ללא טוקן), הכותרות צריכות להופיע
        csp = response.headers.get("content-security-policy", "")
        assert "upgrade-insecure-requests" in csp


class TestSecurityHeadersDebugMode:
    """כותרות אבטחה כש-DEBUG=True — CSP ו-HSTS לא מתווספות, nosniff תמיד."""

    @pytest.mark.unit
    def test_no_hsts_csp_in_debug(self) -> None:
        """במצב DEBUG, ה-middleware לא מוסיף CSP ו-HSTS (חוסמות HTTP מקומי)."""
        app = _build_test_app(debug=True)
        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert "content-security-policy" not in response.headers
            assert "strict-transport-security" not in response.headers

    @pytest.mark.unit
    def test_nosniff_always_present_even_in_debug(self) -> None:
        """X-Content-Type-Options: nosniff מוחל גם במצב DEBUG — בטוח ולא חוסם פיתוח."""
        app = _build_test_app(debug=True)
        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.headers.get("x-content-type-options") == "nosniff"

    @pytest.mark.unit
    def test_security_headers_when_not_debug(self) -> None:
        """כשלא במצב DEBUG, ה-middleware מוסיף את כל הכותרות."""
        app = _build_test_app(debug=False)
        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert "upgrade-insecure-requests" in response.headers.get(
                "content-security-policy", ""
            )
            assert "max-age=" in response.headers.get(
                "strict-transport-security", ""
            )
            assert response.headers.get("x-content-type-options") == "nosniff"
