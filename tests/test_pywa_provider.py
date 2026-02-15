"""
בדיקות ל-PyWaProvider — מימוש WhatsApp Cloud API.

מכסה:
- PyWaProvider — שליחת טקסט, מדיה, retry, circuit breaker
- format_text — המרת HTML ל-WhatsApp markdown
- normalize_phone — נרמול לפורמט Cloud API (ללא +)
- Provider Factory — מצב hybrid (Cloud API לפרטי, WPPConnect לקבוצות)
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from app.core.config import settings
from app.core.exceptions import WhatsAppError
from app.domain.services.whatsapp.pywa_provider import PyWaProvider
from app.domain.services.whatsapp.provider_factory import (
    get_whatsapp_provider,
    get_whatsapp_group_provider,
    reset_providers,
)


async def _passthrough_execute(func, *args, **kwargs):
    """עוזר עבור מוק circuit breaker — מריץ את הפונקציה ועושה await לתוצאה."""
    result = func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


# ============================================================================
# PyWaProvider — שליחת טקסט
# ============================================================================


class TestPyWaSendText:
    """בדיקות שליחת טקסט דרך PyWaProvider."""

    def _make_provider(self) -> tuple[PyWaProvider, AsyncMock]:
        """יצירת provider עם circuit breaker ממוקק שעושה passthrough."""
        cb = CircuitBreaker("test_pywa", CircuitBreakerConfig(failure_threshold=5))
        provider = PyWaProvider(circuit_breaker=cb)
        # מוק ל-circuit breaker שפשוט מריץ את הפונקציה
        cb_mock = AsyncMock(side_effect=_passthrough_execute)
        provider._circuit_breaker.execute = cb_mock
        return provider, cb_mock

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_text_success(self) -> None:
        """שליחת טקסט פשוטה — בדיקה שה-client נקרא עם הפרמטרים הנכונים."""
        provider, _ = self._make_provider()

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=None)
        provider._client = mock_client

        await provider.send_text(to="+972501234567", text="שלום עולם")

        mock_client.send_message.assert_called_once()
        call_kwargs = mock_client.send_message.call_args[1]
        assert call_kwargs["to"] == "972501234567"
        assert call_kwargs["text"] == "שלום עולם"
        assert call_kwargs["buttons"] is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_text_with_keyboard(self) -> None:
        """שליחת טקסט עם כפתורים (3 או פחות) — ממיר ל-Button objects."""
        provider, _ = self._make_provider()

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=None)
        provider._client = mock_client

        # מוק של pywa.types.Button — הייבוא הוא מקומי בתוך _build_buttons
        mock_button_class = MagicMock()
        mock_button_instances = []

        def create_button(**kwargs):
            btn = MagicMock()
            btn.title = kwargs.get("title")
            btn.callback_data = kwargs.get("callback_data")
            mock_button_instances.append(btn)
            return btn

        mock_button_class.side_effect = create_button

        # pywa.types נטען בתוך _build_buttons כ-"from pywa import types as pywa_types"
        mock_pywa_types = MagicMock()
        mock_pywa_types.Button = mock_button_class

        with patch.dict("sys.modules", {"pywa": MagicMock(types=mock_pywa_types), "pywa.types": mock_pywa_types}):
            keyboard = [["כן", "לא"]]
            await provider.send_text(
                to="+972501234567", text="האם להמשיך?", keyboard=keyboard
            )

        # וידוא שנשלחו כפתורים (לא None)
        call_kwargs = mock_client.send_message.call_args[1]
        assert call_kwargs["buttons"] is not None
        assert len(call_kwargs["buttons"]) == 2
        # וידוא שה-Button נוצר עם הפרמטרים הנכונים
        assert mock_button_class.call_count == 2
        first_call = mock_button_class.call_args_list[0]
        assert first_call[1]["title"] == "כן"
        assert first_call[1]["callback_data"] == "כן"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_text_keyboard_fallback(self) -> None:
        """יותר מ-3 כפתורים — הנחיות טקסטואליות ו-buttons=None."""
        provider, _ = self._make_provider()

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=None)
        provider._client = mock_client

        # _build_buttons מייבא pywa.types גם כשיש יותר מ-3 כפתורים (לפני הבדיקה)
        mock_pywa_types = MagicMock()
        with patch.dict("sys.modules", {"pywa": MagicMock(types=mock_pywa_types), "pywa.types": mock_pywa_types}):
            keyboard = [["אפשרות 1", "אפשרות 2", "אפשרות 3", "אפשרות 4"]]
            await provider.send_text(
                to="+972501234567", text="בחר אפשרות:", keyboard=keyboard
            )

        call_kwargs = mock_client.send_message.call_args[1]
        # כפתורים צריכים להיות None כי יש יותר מ-3
        assert call_kwargs["buttons"] is None
        # הטקסט צריך לכלול הנחיות טקסטואליות
        assert "הקלד אחת מהאפשרויות:" in call_kwargs["text"]
        assert "1. אפשרות 1" in call_kwargs["text"]
        assert "4. אפשרות 4" in call_kwargs["text"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_text_normalizes_phone(self) -> None:
        """מספר טלפון מקומי מנורמל לפורמט Cloud API (ללא +)."""
        provider, _ = self._make_provider()

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=None)
        provider._client = mock_client

        await provider.send_text(to="0501234567", text="בדיקה")

        call_kwargs = mock_client.send_message.call_args[1]
        # Cloud API רוצה 972501234567 ולא +972501234567
        assert call_kwargs["to"] == "972501234567"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_text_button_long_label_callback_preserved(self) -> None:
        """כפתור עם תווית ארוכה — callback_data שומר על הערך המלא, title נחתך ל-20."""
        provider, _ = self._make_provider()

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=None)
        provider._client = mock_client

        mock_button_class = MagicMock()
        mock_button_instances = []

        def create_button(**kwargs):
            btn = MagicMock()
            btn.title = kwargs.get("title")
            btn.callback_data = kwargs.get("callback_data")
            mock_button_instances.append(btn)
            return btn

        mock_button_class.side_effect = create_button

        mock_pywa_types = MagicMock()
        mock_pywa_types.Button = mock_button_class

        # תווית ארוכה — יותר מ-20 תווים
        long_label = "🚚 הצטרפות למנוי וקבלת משלוחים"
        assert len(long_label) > 20  # וידוא שהתווית באמת ארוכה

        with patch.dict("sys.modules", {"pywa": MagicMock(types=mock_pywa_types), "pywa.types": mock_pywa_types}):
            # כפתור אחד בלבד (פחות מ-3) כדי שלא יעבור ל-text fallback
            keyboard = [[long_label]]
            await provider.send_text(
                to="+972501234567", text="בחר:", keyboard=keyboard
            )

        # title נחתך ל-20 תווים
        first_call = mock_button_class.call_args_list[0]
        assert len(first_call[1]["title"]) <= 20
        # callback_data שומר על הערך המלא
        assert first_call[1]["callback_data"] == long_label

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_text_retry_on_failure(self) -> None:
        """retry עם exponential backoff על שגיאות זמניות."""
        provider, _ = self._make_provider()

        mock_client = AsyncMock()
        # שגיאה בניסיון ראשון, הצלחה בשני
        mock_client.send_message = AsyncMock(
            side_effect=[Exception("API error"), None]
        )
        provider._client = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await provider.send_text(to="+972501234567", text="retry test")

            # שני ניסיונות: כישלון + הצלחה
            assert mock_client.send_message.call_count == 2
            # בדיקה ש-sleep נקרא עם backoff (2^0 = 1 שנייה)
            mock_sleep.assert_called_once_with(1)


# ============================================================================
# PyWaProvider — שליחת מדיה
# ============================================================================


class TestPyWaSendMedia:
    """בדיקות שליחת מדיה דרך PyWaProvider."""

    def _make_provider(self) -> PyWaProvider:
        cb = CircuitBreaker("test_pywa_media", CircuitBreakerConfig(failure_threshold=5))
        provider = PyWaProvider(circuit_breaker=cb)
        # מוק ל-circuit breaker שפשוט מריץ את הפונקציה
        provider._circuit_breaker.execute = AsyncMock(side_effect=_passthrough_execute)
        return provider

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_image_success(self) -> None:
        """שליחת תמונה — בדיקת קריאה נכונה ל-send_image."""
        provider = self._make_provider()

        mock_client = AsyncMock()
        mock_client.send_image = AsyncMock(return_value=None)
        provider._client = mock_client

        await provider.send_media(
            to="+972501234567",
            media_url="https://example.com/photo.jpg",
            media_type="image",
            caption="תמונה לבדיקה",
        )

        mock_client.send_image.assert_called_once()
        call_kwargs = mock_client.send_image.call_args[1]
        assert call_kwargs["to"] == "972501234567"
        assert call_kwargs["image"] == "https://example.com/photo.jpg"
        assert call_kwargs["caption"] is not None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_document_success(self) -> None:
        """שליחת מסמך — בדיקת קריאה נכונה ל-send_document."""
        provider = self._make_provider()

        mock_client = AsyncMock()
        mock_client.send_document = AsyncMock(return_value=None)
        provider._client = mock_client

        await provider.send_media(
            to="+972501234567",
            media_url="https://example.com/file.pdf",
            media_type="document",
        )

        mock_client.send_document.assert_called_once()
        call_kwargs = mock_client.send_document.call_args[1]
        assert call_kwargs["to"] == "972501234567"
        assert call_kwargs["document"] == "https://example.com/file.pdf"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_media_empty_url_raises(self) -> None:
        """media_url ריק — זורק WhatsAppError."""
        provider = self._make_provider()
        with pytest.raises(WhatsAppError):
            await provider.send_media(to="+972501234567", media_url="")


# ============================================================================
# PyWaProvider — format_text
# ============================================================================


class TestPyWaFormatText:
    """בדיקות המרת HTML ל-WhatsApp markdown."""

    def _make_provider(self) -> PyWaProvider:
        cb = CircuitBreaker("test_pywa_format", CircuitBreakerConfig())
        return PyWaProvider(circuit_breaker=cb)

    @pytest.mark.unit
    def test_format_html_to_whatsapp(self) -> None:
        """המרת תגי HTML לפורמט WhatsApp — bold, italic."""
        provider = self._make_provider()

        result = provider.format_text("<b>כותרת</b> ו-<i>הדגשה</i>")
        assert "*כותרת*" in result
        assert "_הדגשה_" in result


# ============================================================================
# PyWaProvider — normalize_phone
# ============================================================================


class TestPyWaNormalizePhone:
    """בדיקות נרמול מספר טלפון לפורמט Cloud API."""

    def _make_provider(self) -> PyWaProvider:
        cb = CircuitBreaker("test_pywa_phone", CircuitBreakerConfig())
        return PyWaProvider(circuit_breaker=cb)

    @pytest.mark.unit
    def test_normalize_israeli_phone(self) -> None:
        """מספר ישראלי מקומי — 0501234567 הופך ל-972501234567."""
        provider = self._make_provider()
        assert provider.normalize_phone("0501234567") == "972501234567"

    @pytest.mark.unit
    def test_normalize_with_plus(self) -> None:
        """מספר בינלאומי עם + — +972501234567 הופך ל-972501234567."""
        provider = self._make_provider()
        assert provider.normalize_phone("+972501234567") == "972501234567"

    @pytest.mark.unit
    def test_normalize_already_normalized(self) -> None:
        """מספר כבר בפורמט Cloud API — 972501234567 נשאר כמו שהוא."""
        provider = self._make_provider()
        assert provider.normalize_phone("972501234567") == "972501234567"


# ============================================================================
# Provider Factory — מצב hybrid
# ============================================================================


class TestPyWaProviderFactory:
    """בדיקות factory במצב hybrid — Cloud API לפרטי, WPPConnect לקבוצות."""

    @pytest.mark.unit
    def test_hybrid_mode_returns_pywa_for_private(self) -> None:
        """כשמצב hybrid פעיל — get_whatsapp_provider מחזיר PyWaProvider."""
        reset_providers()
        with patch.object(settings, "WHATSAPP_HYBRID_MODE", True):
            provider = get_whatsapp_provider()
            assert provider.provider_name == "pywa"
        reset_providers()

    @pytest.mark.unit
    def test_hybrid_mode_returns_wppconnect_for_group(self) -> None:
        """ספק קבוצות — תמיד WPPConnect גם במצב hybrid."""
        reset_providers()
        with patch.object(settings, "WHATSAPP_HYBRID_MODE", True):
            provider = get_whatsapp_group_provider()
            assert provider.provider_name == "wppconnect"
        reset_providers()

    @pytest.mark.unit
    def test_non_hybrid_returns_wppconnect(self) -> None:
        """מצב רגיל (לא hybrid) — get_whatsapp_provider מחזיר WPPConnect."""
        reset_providers()
        with patch.object(settings, "WHATSAPP_HYBRID_MODE", False), \
             patch.object(settings, "WHATSAPP_PROVIDER", "wppconnect"):
            provider = get_whatsapp_provider()
            assert provider.provider_name == "wppconnect"
        reset_providers()
