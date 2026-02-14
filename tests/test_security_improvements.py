"""
בדיקות לשיפורי אבטחה — Issue #183

1. ולידציית JWT_SECRET_KEY בפרודקשן
2. כותרות אבטחה (Security Headers)
3. אחסון OTP מוצפן (HMAC-SHA256)
"""
import hmac
import warnings
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.auth import _hash_otp, store_otp, verify_otp
from app.core.config import Settings, settings
from app.core.middleware import SecurityHeadersMiddleware


# ============================================================================
# תיקון 1: ולידציית JWT_SECRET_KEY בפרודקשן
# ============================================================================


class TestJWTSecretValidation:
    """ולידציית JWT_SECRET_KEY — חייב לזרוק שגיאה בפרודקשן, אזהרה בפיתוח"""

    @pytest.mark.unit
    def test_empty_jwt_secret_raises_in_production(self):
        """JWT_SECRET_KEY ריק + DEBUG=False → ValueError"""
        with pytest.raises(ValueError, match="JWT_SECRET_KEY ריק בסביבת פרודקשן"):
            Settings(
                JWT_SECRET_KEY="",
                DEBUG=False,
                DATABASE_URL="sqlite+aiosqlite:///:memory:",
            )

    @pytest.mark.unit
    def test_empty_jwt_secret_warns_in_debug(self):
        """JWT_SECRET_KEY ריק + DEBUG=True → אזהרה בלבד, לא קריסה"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            s = Settings(
                JWT_SECRET_KEY="",
                DEBUG=True,
                DATABASE_URL="sqlite+aiosqlite:///:memory:",
            )
            jwt_warnings = [x for x in w if "JWT_SECRET_KEY" in str(x.message)]
            assert len(jwt_warnings) >= 1
            assert s.JWT_SECRET_KEY == ""

    @pytest.mark.unit
    def test_valid_jwt_secret_no_error(self):
        """JWT_SECRET_KEY תקין — לא אמורה להיות שגיאה"""
        s = Settings(
            JWT_SECRET_KEY="a-valid-secret-key-for-testing",
            DEBUG=False,
            DATABASE_URL="sqlite+aiosqlite:///:memory:",
        )
        assert s.JWT_SECRET_KEY == "a-valid-secret-key-for-testing"


# ============================================================================
# תיקון 1.5: ולידציית DEBUG + DB חיצוני
# ============================================================================


class TestDebugWithExternalDB:
    """אזהרה כש-DEBUG=True עם DB שאינו מקומי"""

    @pytest.mark.unit
    def test_debug_with_external_db_warns(self):
        """DEBUG=True + DB חיצוני → אזהרה"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings(
                JWT_SECRET_KEY="test-secret",
                DEBUG=True,
                DATABASE_URL="postgresql+asyncpg://user:pass@production-db.example.com:5432/app",
            )
            debug_warnings = [x for x in w if "DEBUG=True" in str(x.message)]
            assert len(debug_warnings) >= 1

    @pytest.mark.unit
    def test_debug_with_localhost_no_warning(self):
        """DEBUG=True + DB localhost → בלי אזהרה"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings(
                JWT_SECRET_KEY="test-secret",
                DEBUG=True,
                DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/app",
            )
            debug_warnings = [x for x in w if "DEBUG=True" in str(x.message)]
            assert len(debug_warnings) == 0

    @pytest.mark.unit
    def test_debug_with_127_0_0_1_no_warning(self):
        """DEBUG=True + DB 127.0.0.1 → בלי אזהרה"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings(
                JWT_SECRET_KEY="test-secret",
                DEBUG=True,
                DATABASE_URL="postgresql+asyncpg://user:pass@127.0.0.1:5432/app",
            )
            debug_warnings = [x for x in w if "DEBUG=True" in str(x.message)]
            assert len(debug_warnings) == 0

    @pytest.mark.unit
    def test_debug_with_ipv6_loopback_no_warning(self):
        """DEBUG=True + DB ::1 (IPv6 loopback) → בלי אזהרה"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings(
                JWT_SECRET_KEY="test-secret",
                DEBUG=True,
                DATABASE_URL="postgresql+asyncpg://user:pass@::1:5432/app",
            )
            debug_warnings = [x for x in w if "DEBUG=True" in str(x.message)]
            assert len(debug_warnings) == 0


# ============================================================================
# תיקון 2: כותרות אבטחה
# ============================================================================


class TestSecurityHeaders:
    """בדיקות שכותרות אבטחה מוחלות כראוי"""

    @pytest.mark.asyncio
    async def test_nosniff_always_present(self):
        """X-Content-Type-Options: nosniff מוחל גם במצב DEBUG"""
        test_app = FastAPI()

        @test_app.get("/test")
        async def _() -> dict[str, str]:
            return {"ok": "true"}

        test_app.add_middleware(SecurityHeadersMiddleware, debug=True)

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/test")
            assert response.headers.get("X-Content-Type-Options") == "nosniff"

    @pytest.mark.asyncio
    async def test_hsts_only_in_production(self):
        """HSTS מוחל רק כש-DEBUG=False"""
        test_app = FastAPI()

        @test_app.get("/test")
        async def _() -> dict[str, str]:
            return {"ok": "true"}

        test_app.add_middleware(SecurityHeadersMiddleware, debug=True)

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/test")
            assert "Strict-Transport-Security" not in response.headers

    @pytest.mark.asyncio
    async def test_all_headers_in_production(self):
        """כל כותרות האבטחה מוחלות כש-DEBUG=False"""
        test_app = FastAPI()

        @test_app.get("/test")
        async def _() -> dict[str, str]:
            return {"ok": "true"}

        test_app.add_middleware(SecurityHeadersMiddleware, debug=False)

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/test")
            assert response.headers.get("X-Content-Type-Options") == "nosniff"
            assert "Strict-Transport-Security" in response.headers
            assert "Content-Security-Policy" in response.headers


# ============================================================================
# תיקון 3: אחסון OTP מוצפן
# ============================================================================


class TestOTPHashing:
    """בדיקות שה-OTP נשמר כ-HMAC hash ולא כ-plaintext"""

    @pytest.mark.unit
    def test_hash_otp_returns_hmac_sha256(self):
        """_hash_otp מחזיר HMAC-SHA256 hex digest עם מפתח סודי"""
        otp = "123456"
        expected = hmac.new(
            settings.JWT_SECRET_KEY.encode(), otp.encode(), "sha256"
        ).hexdigest()
        assert _hash_otp(otp) == expected
        assert len(_hash_otp(otp)) == 64  # SHA-256 hex = 64 תווים

    @pytest.mark.unit
    def test_hash_otp_is_deterministic(self):
        """אותו OTP מייצר אותו hash"""
        assert _hash_otp("123456") == _hash_otp("123456")

    @pytest.mark.unit
    def test_different_otp_different_hash(self):
        """OTP שונה → hash שונה"""
        assert _hash_otp("123456") != _hash_otp("654321")

    @pytest.mark.unit
    def test_hash_otp_not_plain_sha256(self):
        """HMAC hash שונה מ-SHA-256 ישיר — לא ניתן ל-brute force בלי המפתח"""
        import hashlib
        otp = "123456"
        plain_sha256 = hashlib.sha256(otp.encode()).hexdigest()
        assert _hash_otp(otp) != plain_sha256

    @pytest.mark.asyncio
    async def test_otp_stored_as_hash_not_plaintext(self, fake_redis):
        """OTP נשמר ב-Redis כ-HMAC hash — לא כטקסט פשוט"""
        await store_otp(user_id=999, otp="123456")
        # בדיקה ישירה ב-Redis שהערך הוא hash ולא plaintext
        stored = fake_redis._store.get("panel_otp:999")
        assert stored is not None
        assert stored != "123456"  # לא plaintext
        expected_hmac = hmac.new(
            settings.JWT_SECRET_KEY.encode(), "123456".encode(), "sha256"
        ).hexdigest()
        assert stored == expected_hmac

    @pytest.mark.asyncio
    async def test_otp_verify_works_with_hashing(self):
        """אימות OTP עובד למרות ש-OTP מאוחסן כ-hash"""
        await store_otp(user_id=999, otp="123456")
        result = await verify_otp(user_id=999, otp="123456")
        assert result is True

    @pytest.mark.asyncio
    async def test_wrong_otp_fails_with_hashing(self):
        """OTP שגוי נכשל גם עם hashing"""
        await store_otp(user_id=999, otp="123456")
        result = await verify_otp(user_id=999, otp="000000")
        assert result is False
