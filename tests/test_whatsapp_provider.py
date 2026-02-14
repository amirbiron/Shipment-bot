"""
בדיקות לשכבת ההפשטה של ספק WhatsApp.

מכסה:
- BaseWhatsAppProvider — ממשק אבסטרקטי
- WPPConnectProvider — שליחת טקסט, מדיה, retry, circuit breaker
- Provider Factory — singleton, admin provider, config switch
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from httpx import Response

from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from app.core.exceptions import WhatsAppError
from app.domain.services.whatsapp.base_provider import BaseWhatsAppProvider
from app.domain.services.whatsapp.wppconnect_provider import WPPConnectProvider
from app.domain.services.whatsapp.provider_factory import (
    get_whatsapp_provider,
    get_whatsapp_admin_provider,
    reset_providers,
)


# ============================================================================
# BaseWhatsAppProvider — ממשק אבסטרקטי לא ניתן ליצירה ישירה
# ============================================================================


class TestBaseProviderInterface:
    """וידוא שלא ניתן ליצור instance ישירות מהממשק הבסיסי."""

    @pytest.mark.unit
    def test_cannot_instantiate_abstract_provider(self) -> None:
        """BaseWhatsAppProvider הוא אבסטרקטי — לא ניתן ליצור ישירות."""
        with pytest.raises(TypeError):
            BaseWhatsAppProvider()  # type: ignore[abstract]

    @pytest.mark.unit
    def test_concrete_provider_must_implement_all_methods(self) -> None:
        """ספק חסר מימוש של method אבסטרקטי — TypeError."""

        class IncompleteProvider(BaseWhatsAppProvider):
            # חסר: send_text, send_media, format_text, normalize_phone, provider_name
            pass

        with pytest.raises(TypeError):
            IncompleteProvider()  # type: ignore[abstract]


# ============================================================================
# WPPConnectProvider — שליחת טקסט
# ============================================================================


class TestWPPConnectSendText:
    """בדיקות שליחת טקסט דרך WPPConnectProvider."""

    def _make_provider(self) -> tuple[WPPConnectProvider, CircuitBreaker]:
        cb = CircuitBreaker("test_wa", CircuitBreakerConfig(failure_threshold=5))
        provider = WPPConnectProvider(circuit_breaker=cb)
        return provider, cb

    @pytest.mark.asyncio
    async def test_send_text_success(self) -> None:
        """שליחת טקסט פשוטה — בדיקה שהבקשה נשלחת נכון לגטוויי."""
        provider, _ = self._make_provider()

        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await provider.send_text(to="+972501234567", text="שלום עולם")

            mock_instance.post.assert_called_once()
            call_args = mock_instance.post.call_args
            assert "/send" in call_args[0][0]
            payload = call_args[1]["json"]
            assert payload["phone"] == "+972501234567"
            assert payload["message"] == "שלום עולם"

    @pytest.mark.asyncio
    async def test_send_text_with_keyboard(self) -> None:
        """שליחת טקסט עם כפתורים."""
        provider, _ = self._make_provider()

        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            keyboard = [["כן", "לא"]]
            await provider.send_text(
                to="+972501234567", text="האם להמשיך?", keyboard=keyboard
            )

            payload = mock_instance.post.call_args[1]["json"]
            assert payload["keyboard"] == [["כן", "לא"]]

    @pytest.mark.asyncio
    async def test_send_text_html_conversion(self) -> None:
        """בדיקה שתגי HTML מומרים לפורמט WhatsApp markdown."""
        provider, _ = self._make_provider()

        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await provider.send_text(
                to="+972501234567", text="<b>כותרת</b> רגיל"
            )

            payload = mock_instance.post.call_args[1]["json"]
            # convert_html_to_whatsapp הופך <b> ל-*
            assert "*כותרת*" in payload["message"]

    @pytest.mark.asyncio
    async def test_send_text_retry_on_transient_error(self) -> None:
        """retry על שגיאה זמנית (502) ואז הצלחה."""
        provider, _ = self._make_provider()

        error_response = MagicMock(spec=Response)
        error_response.status_code = 502

        ok_response = MagicMock(spec=Response)
        ok_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(
                side_effect=[error_response, ok_response]
            )
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await provider.send_text(to="+972501234567", text="retry test")

            # שני ניסיונות: כישלון + הצלחה
            assert mock_instance.post.call_count == 2

    @pytest.mark.asyncio
    async def test_send_text_circuit_breaker_opens_on_failures(self) -> None:
        """circuit breaker נפתח אחרי מספיק כשלונות."""
        cb = CircuitBreaker("test_cb_open", CircuitBreakerConfig(failure_threshold=2))
        provider = WPPConnectProvider(circuit_breaker=cb)

        error_response = MagicMock(spec=Response)
        error_response.status_code = 500
        error_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=error_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            # שליחה ראשונה — cb סופר כשלון (send_text זורק exception)
            with pytest.raises(WhatsAppError):
                await provider.send_text(to="+972501234567", text="fail 1")
            # שליחה שנייה — cb סופר כשלון ונפתח
            with pytest.raises(WhatsAppError):
                await provider.send_text(to="+972501234567", text="fail 2")

            assert cb.is_open


# ============================================================================
# WPPConnectProvider — שליחת מדיה
# ============================================================================


class TestWPPConnectSendMedia:
    """בדיקות שליחת מדיה דרך WPPConnectProvider."""

    def _make_provider(self) -> WPPConnectProvider:
        cb = CircuitBreaker("test_media", CircuitBreakerConfig(failure_threshold=5))
        return WPPConnectProvider(circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_send_media_success(self) -> None:
        """שליחת תמונה — בדיקת payload נכון."""
        provider = self._make_provider()

        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await provider.send_media(
                to="+972501234567",
                media_url="https://example.com/photo.jpg",
                media_type="image",
            )

            assert result is True
            payload = mock_instance.post.call_args[1]["json"]
            assert payload["media_url"] == "https://example.com/photo.jpg"
            assert payload["media_type"] == "image"

    @pytest.mark.asyncio
    async def test_send_media_empty_url_returns_false(self) -> None:
        """media_url ריק — מחזיר False בלי לשלוח."""
        provider = self._make_provider()
        result = await provider.send_media(to="+972501234567", media_url="")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_media_failure_returns_false(self) -> None:
        """כשלון בשליחת מדיה — מחזיר False."""
        provider = self._make_provider()

        error_response = MagicMock(spec=Response)
        error_response.status_code = 500
        error_response.text = "Server Error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=error_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await provider.send_media(
                to="+972501234567",
                media_url="https://example.com/photo.jpg",
            )

            assert result is False


# ============================================================================
# WPPConnectProvider — format_text + normalize_phone
# ============================================================================


class TestWPPConnectHelpers:
    """בדיקות פונקציות עזר של WPPConnectProvider."""

    def _make_provider(self) -> WPPConnectProvider:
        cb = CircuitBreaker("test_helpers", CircuitBreakerConfig())
        return WPPConnectProvider(circuit_breaker=cb)

    @pytest.mark.unit
    def test_format_text_converts_bold(self) -> None:
        provider = self._make_provider()
        assert "*שלום*" in provider.format_text("<b>שלום</b>")

    @pytest.mark.unit
    def test_format_text_converts_italic(self) -> None:
        provider = self._make_provider()
        assert "_שלום_" in provider.format_text("<i>שלום</i>")

    @pytest.mark.unit
    def test_normalize_phone_israeli(self) -> None:
        provider = self._make_provider()
        assert provider.normalize_phone("0501234567") == "+972501234567"

    @pytest.mark.unit
    def test_normalize_phone_already_international(self) -> None:
        provider = self._make_provider()
        assert provider.normalize_phone("+972501234567") == "+972501234567"

    @pytest.mark.unit
    def test_provider_name(self) -> None:
        provider = self._make_provider()
        assert provider.provider_name == "wppconnect"


# ============================================================================
# Provider Factory
# ============================================================================


class TestProviderFactory:
    """בדיקות factory ו-singleton."""

    @pytest.mark.unit
    def test_get_provider_returns_wppconnect_by_default(self) -> None:
        """ברירת מחדל — WPPConnectProvider."""
        provider = get_whatsapp_provider()
        assert provider.provider_name == "wppconnect"

    @pytest.mark.unit
    def test_get_provider_is_singleton(self) -> None:
        """factory מחזיר את אותו instance בכל קריאה."""
        p1 = get_whatsapp_provider()
        p2 = get_whatsapp_provider()
        assert p1 is p2

    @pytest.mark.unit
    def test_admin_provider_is_separate_instance(self) -> None:
        """admin provider הוא instance נפרד (circuit breaker נפרד)."""
        user_provider = get_whatsapp_provider()
        admin_provider = get_whatsapp_admin_provider()
        assert user_provider is not admin_provider
        # שניהם WPPConnect
        assert user_provider.provider_name == "wppconnect"
        assert admin_provider.provider_name == "wppconnect"

    @pytest.mark.unit
    def test_admin_provider_uses_admin_circuit_breaker(self) -> None:
        """admin provider משתמש ב-circuit breaker עם שם שונה."""
        user_provider = get_whatsapp_provider()
        admin_provider = get_whatsapp_admin_provider()
        # circuit breakers שונים
        assert user_provider._circuit_breaker.service_name != admin_provider._circuit_breaker.service_name

    @pytest.mark.unit
    def test_reset_providers_clears_singletons(self) -> None:
        """reset_providers מנקה את ה-singletons."""
        p1 = get_whatsapp_provider()
        reset_providers()
        p2 = get_whatsapp_provider()
        assert p1 is not p2

    @pytest.mark.unit
    def test_invalid_provider_type_raises(self) -> None:
        """סוג ספק לא מוכר — ValueError."""
        from app.domain.services.whatsapp.provider_factory import _create_provider

        with pytest.raises(ValueError, match="לא מוכר"):
            _create_provider("nonexistent")
