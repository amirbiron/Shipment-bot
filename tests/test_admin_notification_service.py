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
async def test_whatsapp_photos_sent_even_when_text_message_fails(monkeypatch):
    """תמונות נשלחות גם אם שליחת ההודעה הטקסטית נכשלה"""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_GROUP_ID", "")
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "admin1")
    monkeypatch.setattr(settings, "WHATSAPP_GATEWAY_URL", "http://localhost:3000")

    # הודעה טקסטית נכשלת - גם עם כפתורים וגם בלי (fallback)
    send_mock = AsyncMock(return_value=False)
    photo_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(AdminNotificationService, "_send_whatsapp_admin_message", send_mock)
    monkeypatch.setattr(AdminNotificationService, "_send_whatsapp_admin_photo", photo_mock)

    ok = await AdminNotificationService.notify_new_courier_registration(
        user_id=10,
        full_name="Photo Test",
        service_area="חיפה",
        phone_or_chat_id="972500000000@c.us",
        document_file_id="https://example.com/doc.jpg",
        selfie_file_id="https://example.com/selfie.jpg",
        platform="whatsapp",
    )

    # התמונות נשלחו בהצלחה גם שהטקסט נכשל - success=True
    assert ok is True
    # send_mock נקרא 2 פעמים: פעם עם keyboard ופעם בלי (fallback)
    assert send_mock.await_count == 2
    # photo_mock נקרא 2 פעמים: document + selfie
    assert photo_mock.await_count == 2


@pytest.mark.unit
async def test_whatsapp_keyboard_fallback_on_failure(monkeypatch):
    """אם שליחת הודעה עם כפתורים נכשלת, ננסה בלי כפתורים"""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_GROUP_ID", "")
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "admin1")
    monkeypatch.setattr(settings, "WHATSAPP_GATEWAY_URL", "http://localhost:3000")

    call_args_list = []

    async def _track_send(target, text, keyboard=None):
        call_args_list.append({"target": target, "keyboard": keyboard})
        # נכשל עם keyboard, מצליח בלי
        return keyboard is None

    monkeypatch.setattr(AdminNotificationService, "_send_whatsapp_admin_message", _track_send)
    monkeypatch.setattr(AdminNotificationService, "_send_whatsapp_admin_photo", AsyncMock(return_value=True))

    ok = await AdminNotificationService.notify_new_courier_registration(
        user_id=11,
        full_name="Fallback Test",
        service_area="באר שבע",
        phone_or_chat_id="972500000001@c.us",
        platform="whatsapp",
    )

    assert ok is True
    # קריאה ראשונה עם keyboard, קריאה שנייה בלי
    assert len(call_args_list) == 2
    assert call_args_list[0]["keyboard"] is not None
    assert call_args_list[1]["keyboard"] is None


@pytest.mark.unit
async def test_whatsapp_photos_forwarded_from_telegram_registration(monkeypatch):
    """מסמכי טלגרם נשלחים לוואטסאפ מנהלים כ-data URL"""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", "")
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_GROUP_ID", "wa-group")
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "")
    monkeypatch.setattr(settings, "WHATSAPP_GATEWAY_URL", "http://localhost:3000")

    send_mock = AsyncMock(return_value=True)
    photo_mock = AsyncMock(return_value=True)
    download_mock = AsyncMock(
        side_effect=[
            "data:image/jpeg;base64,AAAA",
            "data:image/jpeg;base64,BBBB",
        ]
    )

    monkeypatch.setattr(AdminNotificationService, "_send_whatsapp_admin_message", send_mock)
    monkeypatch.setattr(AdminNotificationService, "_send_whatsapp_admin_photo", photo_mock)
    monkeypatch.setattr(
        AdminNotificationService,
        "_download_telegram_file_as_data_url",
        download_mock,
    )

    ok = await AdminNotificationService.notify_new_courier_registration(
        user_id=12,
        full_name="Telegram Courier",
        service_area="תל אביב",
        phone_or_chat_id="6865105071",
        document_file_id="tg-file-1",
        selfie_file_id="tg-file-2",
        platform="telegram",
    )

    assert ok is True
    assert photo_mock.await_count == 2
    photo_mock.assert_any_await("wa-group", "data:image/jpeg;base64,AAAA")
    photo_mock.assert_any_await("wa-group", "data:image/jpeg;base64,BBBB")


@pytest.mark.unit
async def test_forward_photo_records_success_on_send_photo(monkeypatch):
    """sendPhoto שמצליח מדווח הצלחה ל-circuit breaker (לא נשאר תקוע ב-HALF_OPEN)"""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")

    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 200

    mock_instance = AsyncMock()
    mock_instance.post = AsyncMock(return_value=mock_response)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=None)

    # CB מדומה שעוקב אחרי record_success
    class _TrackingCB:
        success_count = 0

        async def can_execute(self):
            return True

        async def record_success(self):
            _TrackingCB.success_count += 1

        async def record_failure(self, e=None):
            pass

        async def execute(self, func, *args, **kwargs):
            return await func(*args, **kwargs)

    tracking_cb = _TrackingCB()
    monkeypatch.setattr(
        "app.domain.services.admin_notification_service.get_telegram_circuit_breaker",
        lambda: tracking_cb,
    )

    with patch("app.domain.services.admin_notification_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value = mock_instance

        ok = await AdminNotificationService._forward_photo("chat-1", "file-123")

    assert ok is True
    assert tracking_cb.success_count == 1


@pytest.mark.unit
async def test_forward_photo_no_double_can_execute(monkeypatch):
    """can_execute נקרא פעם אחת בלבד (לא צורך slots מיותרים ב-HALF_OPEN)"""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")

    # sendPhoto נכשל (400) → fallback ל-sendDocument שמצליח
    call_count = 0

    async def _mock_post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock(spec=Response)
        if "sendPhoto" in url:
            resp.status_code = 400
            resp.text = "Bad Request: wrong file"
        else:
            resp.status_code = 200
        return resp

    mock_instance = AsyncMock()
    mock_instance.post = _mock_post
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=None)

    class _CountingCB:
        can_execute_count = 0
        success_recorded = False

        async def can_execute(self):
            _CountingCB.can_execute_count += 1
            return True

        async def record_success(self):
            _CountingCB.success_recorded = True

        async def record_failure(self, e=None):
            pass

        async def execute(self, func, *args, **kwargs):
            return await func(*args, **kwargs)

    counting_cb = _CountingCB()
    monkeypatch.setattr(
        "app.domain.services.admin_notification_service.get_telegram_circuit_breaker",
        lambda: counting_cb,
    )

    with patch("app.domain.services.admin_notification_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value = mock_instance

        ok = await AdminNotificationService._forward_photo("chat-1", "doc-file")

    assert ok is True
    # can_execute נקרא פעם אחת בלבד (לא דרך cb.execute)
    assert counting_cb.can_execute_count == 1
    assert counting_cb.success_recorded is True


@pytest.mark.unit
async def test_forward_photo_fast_fails_when_cb_open(monkeypatch):
    """כש-CB פתוח (טלגרם למטה) — fast-fail, לא מנסים לשלוח בכלל"""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")

    class _OpenCB:
        async def can_execute(self):
            return False

        async def record_success(self):
            pass

        async def record_failure(self, e=None):
            pass

    monkeypatch.setattr(
        "app.domain.services.admin_notification_service.get_telegram_circuit_breaker",
        lambda: _OpenCB(),
    )

    # לא צריכים mock ל-httpx כי הקוד לא אמור להגיע אליו בכלל
    with patch("app.domain.services.admin_notification_service.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value = mock_instance

        ok = await AdminNotificationService._forward_photo("chat-1", "file-123")

    assert ok is False
    # לא נעשתה שום קריאת HTTP
    mock_instance.post.assert_not_awaited()


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

