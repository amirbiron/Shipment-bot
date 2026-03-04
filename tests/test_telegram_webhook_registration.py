"""
בדיקות יחידה לרישום webhook אוטומטי של טלגרם בעלייה.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx


class TestRegisterTelegramWebhook:
    """בדיקות ל-_register_telegram_webhook() — רישום אוטומטי ב-startup."""

    @pytest.mark.unit
    async def test_skips_when_no_bot_token(self) -> None:
        """מדלג כשאין TELEGRAM_BOT_TOKEN."""
        with patch("app.main.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN = None

            from app.main import _register_telegram_webhook
            await _register_telegram_webhook()
            # לא אמור לנסות לשלוח בקשה — אם הגענו לכאן בלי exception, עבר

    @pytest.mark.unit
    async def test_skips_when_no_base_url(self) -> None:
        """מדלג כשאין URL חיצוני מוגדר."""
        with patch("app.main.settings") as mock_settings, \
             patch.dict("os.environ", {}, clear=False):
            mock_settings.TELEGRAM_BOT_TOKEN = "test-token"
            mock_settings.TELEGRAM_WEBHOOK_BASE_URL = ""
            # וודא שגם RENDER_EXTERNAL_URL לא מוגדר
            import os
            os.environ.pop("RENDER_EXTERNAL_URL", None)

            from app.main import _register_telegram_webhook
            await _register_telegram_webhook()

    @pytest.mark.unit
    async def test_registers_webhook_successfully(self) -> None:
        """רושם webhook בהצלחה עם URL מלא."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "description": "Webhook was set"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.main.settings") as mock_settings, \
             patch("app.main.httpx.AsyncClient", return_value=mock_client):
            mock_settings.TELEGRAM_BOT_TOKEN = "123:ABC"
            mock_settings.TELEGRAM_WEBHOOK_BASE_URL = "https://my-app.onrender.com"
            mock_settings.TELEGRAM_WEBHOOK_SECRET_TOKEN = ""

            from app.main import _register_telegram_webhook
            await _register_telegram_webhook()

        # בודק שהקריאה הייתה עם הפרמטרים הנכונים
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "setWebhook" in call_args[0][0]
        assert call_args[1]["data"]["url"] == "https://my-app.onrender.com/api/telegram/webhook"

    @pytest.mark.unit
    async def test_registers_with_secret_token(self) -> None:
        """כולל secret_token ברישום כשהוא מוגדר."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.main.settings") as mock_settings, \
             patch("app.main.httpx.AsyncClient", return_value=mock_client):
            mock_settings.TELEGRAM_BOT_TOKEN = "123:ABC"
            mock_settings.TELEGRAM_WEBHOOK_BASE_URL = "https://my-app.onrender.com"
            mock_settings.TELEGRAM_WEBHOOK_SECRET_TOKEN = "my-secret-token"

            from app.main import _register_telegram_webhook
            await _register_telegram_webhook()

        call_args = mock_client.post.call_args
        assert call_args[1]["data"]["secret_token"] == "my-secret-token"

    @pytest.mark.unit
    async def test_uses_render_external_url_fallback(self) -> None:
        """משתמש ב-RENDER_EXTERNAL_URL כשאין TELEGRAM_WEBHOOK_BASE_URL."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.main.settings") as mock_settings, \
             patch("app.main.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {"RENDER_EXTERNAL_URL": "https://render-app.onrender.com"}):
            mock_settings.TELEGRAM_BOT_TOKEN = "123:ABC"
            mock_settings.TELEGRAM_WEBHOOK_BASE_URL = ""
            mock_settings.TELEGRAM_WEBHOOK_SECRET_TOKEN = ""

            from app.main import _register_telegram_webhook
            await _register_telegram_webhook()

        call_args = mock_client.post.call_args
        assert call_args[1]["data"]["url"] == "https://render-app.onrender.com/api/telegram/webhook"

    @pytest.mark.unit
    async def test_handles_api_failure_gracefully(self) -> None:
        """לא קורס כשקריאת API נכשלת — רק לוג שגיאה."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "description": "Unauthorized"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.main.settings") as mock_settings, \
             patch("app.main.httpx.AsyncClient", return_value=mock_client):
            mock_settings.TELEGRAM_BOT_TOKEN = "bad-token"
            mock_settings.TELEGRAM_WEBHOOK_BASE_URL = "https://my-app.onrender.com"
            mock_settings.TELEGRAM_WEBHOOK_SECRET_TOKEN = ""

            from app.main import _register_telegram_webhook
            # לא אמור לזרוק exception
            await _register_telegram_webhook()

    @pytest.mark.unit
    async def test_handles_network_error_gracefully(self) -> None:
        """לא קורס כשיש שגיאת רשת — רק לוג שגיאה."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.main.settings") as mock_settings, \
             patch("app.main.httpx.AsyncClient", return_value=mock_client):
            mock_settings.TELEGRAM_BOT_TOKEN = "123:ABC"
            mock_settings.TELEGRAM_WEBHOOK_BASE_URL = "https://my-app.onrender.com"
            mock_settings.TELEGRAM_WEBHOOK_SECRET_TOKEN = ""

            from app.main import _register_telegram_webhook
            # לא אמור לזרוק exception
            await _register_telegram_webhook()
