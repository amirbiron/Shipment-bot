"""
בדיקות לאימות חתימת webhook מטלגרם (X-Telegram-Bot-Api-Secret-Token).
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.core.config import settings
from app.api.dependencies.webhook_auth import verify_telegram_webhook_token


def _make_mock_request(client_ip: str = "127.0.0.1") -> MagicMock:
    """יצירת אובייקט Request מדומה לבדיקות יחידה."""
    request = MagicMock()
    request.client.host = client_ip
    request.headers.get.return_value = None  # אין X-Forwarded-For
    return request


def _no_ip_block():
    """mock שמונע חסימת IP בבדיקות — יוצר AsyncMock חדש בכל טסט למניעת דליפת state."""
    return patch(
        "app.api.dependencies.webhook_signature._is_ip_blocked",
        new=AsyncMock(return_value=False),
    )


def _no_record_attempt():
    """mock שמונע רישום ניסיון כושל — יוצר AsyncMock חדש בכל טסט."""
    return patch(
        "app.api.dependencies.webhook_signature._record_failed_attempt",
        new=AsyncMock(),
    )


# ──────────────────────────────────────────────
#  בדיקות יחידה ל-dependency
# ──────────────────────────────────────────────

class TestVerifyTelegramWebhookToken:
    """בדיקות ישירות ל-dependency — ללא HTTP."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_skips_when_secret_not_configured_debug(self) -> None:
        """DEBUG=True + TELEGRAM_WEBHOOK_SECRET_TOKEN ריק — מדלג ללא שגיאה."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", ""), \
             patch.object(settings, "DEBUG", True):
            # לא צריך לזרוק exception
            await verify_telegram_webhook_token(_make_mock_request(), None)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_blocks_when_secret_not_configured_production(self) -> None:
        """DEBUG=False + TELEGRAM_WEBHOOK_SECRET_TOKEN ריק — 403."""
        from fastapi import HTTPException
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", ""), \
             patch.object(settings, "DEBUG", False):
            with pytest.raises(HTTPException) as exc_info:
                await verify_telegram_webhook_token(None)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_missing_header(self) -> None:
        """אם הטוקן מוגדר אבל הכותרת חסרה — 403."""
        from fastapi import HTTPException
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "my-secret"), \
             _no_ip_block(), _no_record_attempt():
            with pytest.raises(HTTPException) as exc_info:
                await verify_telegram_webhook_token(_make_mock_request(), None)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_wrong_token(self) -> None:
        """אם הטוקן לא תואם — 403."""
        from fastapi import HTTPException
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "my-secret"), \
             _no_ip_block(), _no_record_attempt():
            with pytest.raises(HTTPException) as exc_info:
                await verify_telegram_webhook_token(_make_mock_request(), "wrong-token")
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_accepts_valid_token(self) -> None:
        """אם הטוקן תואם — עובר ללא שגיאה."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "my-secret"), \
             _no_ip_block():
            await verify_telegram_webhook_token(_make_mock_request(), "my-secret")


# ──────────────────────────────────────────────
#  בדיקות אינטגרציה — HTTP מלא
# ──────────────────────────────────────────────

_VALID_UPDATE = {
    "update_id": 100,
    "message": {
        "message_id": 1,
        "chat": {"id": 12345, "type": "private"},
        "text": "שלום",
        "date": 1700000000,
        "from": {"id": 12345, "first_name": "Test"},
    },
}


class TestWebhookAuthIntegration:
    """בדיקות HTTP דרך test_client — ולידציה שה-dependency מחובר ל-endpoint."""

    @pytest.mark.asyncio
    async def test_webhook_rejects_missing_token_when_configured(self, test_client) -> None:
        """כותרת חסרה + טוקן מוגדר → 403."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "test-secret-abc"), \
             _no_ip_block(), _no_record_attempt():
            resp = await test_client.post("/api/telegram/webhook", json=_VALID_UPDATE)
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_webhook_rejects_wrong_token(self, test_client) -> None:
        """כותרת שגויה + טוקן מוגדר → 403."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "test-secret-abc"), \
             _no_ip_block(), _no_record_attempt():
            resp = await test_client.post(
                "/api/telegram/webhook",
                json=_VALID_UPDATE,
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-value"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_webhook_accepts_valid_token(self, test_client) -> None:
        """כותרת תואמת → 200."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "test-secret-abc"), \
             _no_ip_block():
            resp = await test_client.post(
                "/api/telegram/webhook",
                json=_VALID_UPDATE,
                headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret-abc"},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_webhook_passes_when_secret_not_configured_debug(self, test_client) -> None:
        """DEBUG=True + טוקן לא מוגדר → webhook עובר ללא אימות (פיתוח מקומי)."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", ""), \
             patch.object(settings, "DEBUG", True):
            resp = await test_client.post("/api/telegram/webhook", json=_VALID_UPDATE)
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_webhook_blocked_when_secret_not_configured_production(self, test_client) -> None:
        """DEBUG=False + טוקן לא מוגדר → 403 — webhook חסום בפרודקשן."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", ""), \
             patch.object(settings, "DEBUG", False):
            resp = await test_client.post("/api/telegram/webhook", json=_VALID_UPDATE)
            assert resp.status_code == 403
