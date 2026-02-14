"""
בדיקות לאימות חתימת webhook מטלגרם (X-Telegram-Bot-Api-Secret-Token).
"""
import pytest
from unittest.mock import patch

from app.core.config import settings
from app.api.dependencies.webhook_auth import verify_telegram_webhook_token


# ──────────────────────────────────────────────
#  בדיקות יחידה ל-dependency
# ──────────────────────────────────────────────

class TestVerifyTelegramWebhookToken:
    """בדיקות ישירות ל-dependency — ללא HTTP."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_skips_when_secret_not_configured(self) -> None:
        """אם TELEGRAM_WEBHOOK_SECRET_TOKEN ריק — מדלג ללא שגיאה."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", ""):
            # לא צריך לזרוק exception
            await verify_telegram_webhook_token(None)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_missing_header(self) -> None:
        """אם הטוקן מוגדר אבל הכותרת חסרה — 403."""
        from fastapi import HTTPException
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "my-secret"):
            with pytest.raises(HTTPException) as exc_info:
                await verify_telegram_webhook_token(None)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_wrong_token(self) -> None:
        """אם הטוקן לא תואם — 403."""
        from fastapi import HTTPException
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "my-secret"):
            with pytest.raises(HTTPException) as exc_info:
                await verify_telegram_webhook_token("wrong-token")
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_accepts_valid_token(self) -> None:
        """אם הטוקן תואם — עובר ללא שגיאה."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "my-secret"):
            await verify_telegram_webhook_token("my-secret")


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
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "test-secret-abc"):
            resp = await test_client.post("/api/telegram/webhook", json=_VALID_UPDATE)
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_webhook_rejects_wrong_token(self, test_client) -> None:
        """כותרת שגויה + טוקן מוגדר → 403."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "test-secret-abc"):
            resp = await test_client.post(
                "/api/telegram/webhook",
                json=_VALID_UPDATE,
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-value"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_webhook_accepts_valid_token(self, test_client) -> None:
        """כותרת תואמת → 200."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "test-secret-abc"):
            resp = await test_client.post(
                "/api/telegram/webhook",
                json=_VALID_UPDATE,
                headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret-abc"},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_webhook_passes_when_secret_not_configured(self, test_client) -> None:
        """טוקן לא מוגדר → webhook עובר ללא אימות (תאימות לאחור)."""
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", ""):
            resp = await test_client.post("/api/telegram/webhook", json=_VALID_UPDATE)
            assert resp.status_code == 200
