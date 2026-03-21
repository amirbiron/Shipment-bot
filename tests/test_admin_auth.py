"""
בדיקות יחידה לכניסת אדמין דרך Telegram Login Widget ו-admin dependency מעודכנת.
"""
import hashlib
import hmac
import time

import pytest
from unittest.mock import patch

import httpx

from app.core.auth import create_access_token
from app.core.config import settings
from app.db.models.user import UserRole

_TEST_BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
_TEST_API_KEY = "test-admin-api-key-for-tests"


def _make_telegram_auth_data(
    telegram_id: int = 123456789,
    first_name: str = "Admin",
    auth_date: int | None = None,
    bot_token: str = _TEST_BOT_TOKEN,
) -> dict:
    """יצירת נתוני אימות טלגרם תקפים עם hash נכון"""
    if auth_date is None:
        auth_date = int(time.time())
    data = {
        "id": telegram_id,
        "first_name": first_name,
        "auth_date": auth_date,
    }
    check_pairs = sorted(f"{k}={v}" for k, v in data.items())
    data_check_string = "\n".join(check_pairs)
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    data["hash"] = computed_hash
    return data


def _patch_bot_token():
    """mock ל-TELEGRAM_BOT_TOKEN — מחזיר patch חדש כל פעם"""
    return patch.object(settings, "TELEGRAM_BOT_TOKEN", _TEST_BOT_TOKEN)


# ============================================================================
# כניסת אדמין דרך טלגרם
# ============================================================================


class TestAdminTelegramLogin:
    """בדיקות API endpoint לכניסת אדמין דרך Telegram Login Widget"""

    @pytest.mark.asyncio
    async def test_admin_telegram_login_success(
        self, test_client: httpx.AsyncClient, user_factory,
    ) -> None:
        """כניסה מוצלחת של אדמין דרך טלגרם — מחזיר JWT"""
        await user_factory(
            phone_number="+972501111111",
            name="אדמין ראשי",
            role=UserRole.ADMIN,
            platform="telegram",
            telegram_chat_id="123456789",
        )

        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=123456789)
            response = await test_client.post(
                "/api/admin/auth/telegram-login", json=auth_data,
            )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["username"] == "אדמין ראשי"

    @pytest.mark.asyncio
    async def test_admin_telegram_login_non_admin_rejected(
        self, test_client: httpx.AsyncClient, user_factory,
    ) -> None:
        """כניסת אדמין דרך טלגרם עם תפקיד שאינו אדמין — 401"""
        await user_factory(
            phone_number="+972501111111",
            role=UserRole.STATION_OWNER,
            telegram_chat_id="123456789",
        )

        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=123456789)
            response = await test_client.post(
                "/api/admin/auth/telegram-login", json=auth_data,
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_admin_telegram_login_unknown_user(
        self, test_client: httpx.AsyncClient,
    ) -> None:
        """כניסת אדמין דרך טלגרם עם משתמש לא קיים — 401"""
        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=999999999)
            response = await test_client.post(
                "/api/admin/auth/telegram-login", json=auth_data,
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_admin_telegram_login_invalid_hash(
        self, test_client: httpx.AsyncClient, user_factory,
    ) -> None:
        """כניסת אדמין דרך טלגרם עם hash שגוי — 401"""
        await user_factory(
            phone_number="+972501111111",
            role=UserRole.ADMIN,
            telegram_chat_id="123456789",
        )

        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=123456789)
            auth_data["hash"] = "invalid_hash"
            response = await test_client.post(
                "/api/admin/auth/telegram-login", json=auth_data,
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_admin_telegram_login_inactive_user_rejected(
        self, test_client: httpx.AsyncClient, user_factory,
    ) -> None:
        """כניסת אדמין דרך טלגרם למשתמש לא פעיל — 401"""
        await user_factory(
            phone_number="+972501111111",
            role=UserRole.ADMIN,
            telegram_chat_id="123456789",
            is_active=False,
        )

        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=123456789)
            response = await test_client.post(
                "/api/admin/auth/telegram-login", json=auth_data,
            )
        assert response.status_code == 401


# ============================================================================
# admin dependency — תמיכה ב-JWT אדמין
# ============================================================================


class TestAdminDependencyWithJWT:
    """בדיקות שה-admin dependency מקבלת גם JWT Bearer עם role=admin"""

    @pytest.fixture(autouse=True)
    def set_admin_api_key(self):
        with patch.object(settings, "ADMIN_API_KEY", _TEST_API_KEY):
            yield

    @pytest.mark.asyncio
    async def test_admin_jwt_bearer_accepted(
        self, test_client: httpx.AsyncClient,
    ) -> None:
        """JWT עם role=admin מתקבל ב-admin endpoints"""
        token = create_access_token(user_id=1, station_id=0, role="admin")
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_non_admin_jwt_rejected(
        self, test_client: httpx.AsyncClient,
    ) -> None:
        """JWT עם role שאינו admin — 403"""
        token = create_access_token(user_id=1, station_id=1, role="station_owner")
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_api_key_still_works(
        self, test_client: httpx.AsyncClient,
    ) -> None:
        """API key עדיין עובד כמו קודם"""
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers={"X-Admin-API-Key": _TEST_API_KEY},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_no_auth_returns_401(
        self, test_client: httpx.AsyncClient,
    ) -> None:
        """בקשה ללא שום אימות — 401"""
        response = await test_client.get("/api/admin/debug/circuit-breakers")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_jwt_returns_403(
        self, test_client: httpx.AsyncClient,
    ) -> None:
        """JWT לא תקין — 403"""
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_wrong_api_key_with_valid_jwt_falls_back(
        self, test_client: httpx.AsyncClient,
    ) -> None:
        """מפתח API שגוי + JWT תקין — JWT גובר (fallback)"""
        token = create_access_token(user_id=1, station_id=0, role="admin")
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers={
                "X-Admin-API-Key": "wrong-key",
                "Authorization": f"Bearer {token}",
            },
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_wrong_api_key_without_jwt_returns_403(
        self, test_client: httpx.AsyncClient,
    ) -> None:
        """מפתח API שגוי ללא JWT — 403"""
        response = await test_client.get(
            "/api/admin/debug/circuit-breakers",
            headers={"X-Admin-API-Key": "wrong-key"},
        )
        assert response.status_code == 403
