"""
בדיקות ניתוב היברידי — לוגיקת ניתוב הודעות לספק הנכון.

מכסה:
- _is_group_target — זיהוי קבוצות לפי סיומת @g.us
- send_whatsapp_message — ניתוב לספק קבוצה/פרטי
- _send_whatsapp_message (Celery) — ניתוב לספק קבוצה/פרטי
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.webhooks.whatsapp import _is_group_target, send_whatsapp_message
from app.workers.tasks import _send_whatsapp_message


# ============================================================================
# TestIsGroupTarget — זיהוי יעד קבוצה לפי סיומת @g.us
# ============================================================================


class TestIsGroupTarget:
    """בדיקות לפונקציית _is_group_target — זיהוי קבוצות WhatsApp."""

    @pytest.mark.unit
    def test_group_id_detected(self) -> None:
        """מזהה קבוצה עם סיומת @g.us — מזוהה כקבוצה."""
        assert _is_group_target("123456@g.us") is True

    @pytest.mark.unit
    def test_phone_number_not_group(self) -> None:
        """מספר טלפון רגיל — לא קבוצה."""
        assert _is_group_target("+972501234567") is False

    @pytest.mark.unit
    def test_lid_not_group(self) -> None:
        """מזהה LID (@lid) — לא קבוצה."""
        assert _is_group_target("123@lid") is False


# ============================================================================
# TestSendWhatsappMessageRouting — ניתוב send_whatsapp_message לספק הנכון
# ============================================================================


class TestSendWhatsappMessageRouting:
    """בדיקות ניתוב הודעות ב-send_whatsapp_message לפי סוג יעד."""

    @pytest.mark.unit
    async def test_group_target_uses_group_provider(self) -> None:
        """יעד קבוצה (@g.us) — משתמש בספק קבוצות (WPPConnect)."""
        mock_group_provider = MagicMock()
        mock_group_provider.format_text = MagicMock(return_value="formatted text")
        mock_group_provider.send_text = AsyncMock()

        mock_main_provider = MagicMock()
        mock_main_provider.format_text = MagicMock(return_value="formatted text")
        mock_main_provider.send_text = AsyncMock()

        with patch(
            "app.api.webhooks.whatsapp.get_whatsapp_group_provider",
            return_value=mock_group_provider,
        ), patch(
            "app.api.webhooks.whatsapp.get_whatsapp_provider",
            return_value=mock_main_provider,
        ):
            await send_whatsapp_message("123456@g.us", "הודעה לקבוצה")

        mock_group_provider.send_text.assert_called_once()
        mock_main_provider.send_text.assert_not_called()

    @pytest.mark.unit
    async def test_private_target_uses_main_provider(self) -> None:
        """יעד פרטי (מספר טלפון) — משתמש בספק הראשי (Cloud API במצב hybrid)."""
        mock_group_provider = MagicMock()
        mock_group_provider.format_text = MagicMock(return_value="formatted text")
        mock_group_provider.send_text = AsyncMock()

        mock_main_provider = MagicMock()
        mock_main_provider.format_text = MagicMock(return_value="formatted text")
        mock_main_provider.send_text = AsyncMock()

        with patch(
            "app.api.webhooks.whatsapp.get_whatsapp_group_provider",
            return_value=mock_group_provider,
        ), patch(
            "app.api.webhooks.whatsapp.get_whatsapp_provider",
            return_value=mock_main_provider,
        ):
            await send_whatsapp_message("+972501234567", "הודעה פרטית")

        mock_main_provider.send_text.assert_called_once()
        mock_group_provider.send_text.assert_not_called()


# ============================================================================
# TestCeleryRoutingLogic — ניתוב ב-_send_whatsapp_message (Celery worker)
# ============================================================================


class TestCeleryRoutingLogic:
    """בדיקות ניתוב הודעות ב-_send_whatsapp_message של Celery."""

    @pytest.mark.unit
    async def test_group_phone_uses_group_provider(self) -> None:
        """יעד קבוצה (@g.us) — Celery משתמש בספק קבוצות."""
        mock_group_provider = MagicMock()
        mock_group_provider.format_text = MagicMock(return_value="formatted text")
        mock_group_provider.send_text = AsyncMock()

        mock_main_provider = MagicMock()
        mock_main_provider.format_text = MagicMock(return_value="formatted text")
        mock_main_provider.send_text = AsyncMock()

        with patch(
            "app.workers.tasks.get_whatsapp_group_provider",
            return_value=mock_group_provider,
        ), patch(
            "app.workers.tasks.get_whatsapp_provider",
            return_value=mock_main_provider,
        ):
            result = await _send_whatsapp_message(
                "123456@g.us", {"message_text": "הודעה לקבוצה"}
            )

        assert result is True
        mock_group_provider.send_text.assert_called_once()
        mock_main_provider.send_text.assert_not_called()

    @pytest.mark.unit
    async def test_private_phone_uses_main_provider(self) -> None:
        """יעד פרטי (מספר טלפון) — Celery משתמש בספק הראשי."""
        mock_group_provider = MagicMock()
        mock_group_provider.format_text = MagicMock(return_value="formatted text")
        mock_group_provider.send_text = AsyncMock()

        mock_main_provider = MagicMock()
        mock_main_provider.format_text = MagicMock(return_value="formatted text")
        mock_main_provider.send_text = AsyncMock()

        with patch(
            "app.workers.tasks.get_whatsapp_group_provider",
            return_value=mock_group_provider,
        ), patch(
            "app.workers.tasks.get_whatsapp_provider",
            return_value=mock_main_provider,
        ):
            result = await _send_whatsapp_message(
                "+972501234567", {"message_text": "הודעה פרטית"}
            )

        assert result is True
        mock_main_provider.send_text.assert_called_once()
        mock_group_provider.send_text.assert_not_called()
