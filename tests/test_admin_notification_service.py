"""
Unit tests for AdminNotificationService.

Focus:
- Behavior based on configuration (telegram/whatsapp enabled/disabled)
- Internal send helpers handle non-200 responses gracefully
"""

from __future__ import annotations

import pytest
from httpx import Response
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import settings
from app.domain.services.admin_notification_service import AdminNotificationService


class _DummyCircuitBreaker:
    async def execute(self, func, *args, **kwargs):  # noqa: ANN001, D401
        return await func(*args, **kwargs)


@pytest.mark.unit
async def test_notify_new_courier_registration_returns_false_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_GROUP_ID", None)
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", "")

    ok = await AdminNotificationService.notify_new_courier_registration(
        user_id=1,
        full_name="Test Courier",
        service_area="תל אביב",
        phone_or_chat_id="123",
        document_file_id=None,
        platform="telegram",
    )
    assert ok is False


@pytest.mark.unit
async def test_notify_new_courier_registration_sends_telegram_and_forwards_photo(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_GROUP_ID", None)
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "")
    # הגדרת מנהל פרטי - כפתורי inline נשלחים רק בצ'אט פרטי
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", "admin-chat")

    send_mock = AsyncMock(return_value=True)
    forward_mock = AsyncMock(return_value=True)

    # השירות משתמש עכשיו ב-inline keyboard עם כפתורי אישור/דחייה
    monkeypatch.setattr(AdminNotificationService, "_send_telegram_message_with_inline_keyboard", send_mock)
    monkeypatch.setattr(AdminNotificationService, "_forward_photo", forward_mock)

    ok = await AdminNotificationService.notify_new_courier_registration(
        user_id=123,
        full_name="Test Courier",
        service_area="ירושלים",
        phone_or_chat_id="999",
        document_file_id="file-1",
        platform="telegram",
    )

    assert ok is True
    send_mock.assert_awaited_once()
    forward_mock.assert_awaited_once_with("admin-chat", "file-1")


@pytest.mark.unit
async def test_notify_new_courier_registration_sends_whatsapp_and_sends_media(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_GROUP_ID", "wa-group")
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "")
    monkeypatch.setattr(settings, "WHATSAPP_GATEWAY_URL", "http://localhost:3000")

    send_mock = AsyncMock(return_value=True)
    photo_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(AdminNotificationService, "_send_whatsapp_admin_message", send_mock)
    monkeypatch.setattr(AdminNotificationService, "_send_whatsapp_admin_photo", photo_mock)

    ok = await AdminNotificationService.notify_new_courier_registration(
        user_id=7,
        full_name="Test Courier",
        service_area="חיפה",
        phone_or_chat_id="972500000000@c.us",
        document_file_id="https://example.com/media.jpg",
        platform="whatsapp",
    )

    assert ok is True
    send_mock.assert_awaited_once()
    photo_mock.assert_awaited_once_with("wa-group", "https://example.com/media.jpg")


@pytest.mark.unit
async def test_send_telegram_message_non_200_returns_false(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "admin-chat")
    monkeypatch.setattr(
        "app.domain.services.admin_notification_service.get_telegram_circuit_breaker",
        lambda: _DummyCircuitBreaker(),
    )

    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 500
    mock_response.text = "fail"

    mock_instance = AsyncMock()
    mock_instance.post = AsyncMock(return_value=mock_response)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=None)

    with patch("app.domain.services.admin_notification_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value = mock_instance

        ok = await AdminNotificationService._send_telegram_message("chat-1", "hello")

    assert ok is False

