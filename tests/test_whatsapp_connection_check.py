"""
בדיקות יחידה ל-check_whatsapp_connection — בדיקת חיבור WhatsApp Gateway תקופתית.

מכסה:
- Gateway מחובר — מחזיר connected, מנקה throttle
- Gateway מנותק (session disconnected) — שולח התראה למנהלים
- Gateway לא זמין (HTTP error) — שולח התראה
- Gateway timeout — שולח התראה
- Gateway unreachable — שולח התראה
- Throttling — לא שולח התראה כפולה ב-15 דקות
- ניקוי throttle אחרי חזרה לתקינות
"""
import asyncio
import concurrent.futures
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _run_task_in_thread(task_fn):
    """מריץ Celery task (sync) בתוך thread — עוקף nested event loop."""
    from app.core.logging import set_correlation_id

    def _in_thread():
        set_correlation_id()
        loop = asyncio.new_event_loop()
        try:
            # מוק ל-run_async שמשתמש ב-loop חדש
            return task_fn()
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_in_thread)
        return future.result(timeout=30)


def _patch_run_async():
    """מוק ל-run_async שמאפשר להריץ Celery task מתוך בדיקה async."""
    import concurrent.futures

    def _test_run_async(coro):
        from app.core.logging import set_correlation_id
        set_correlation_id()

        def _run_in_thread():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_in_thread)
            return future.result(timeout=30)

    return patch("app.workers.tasks.run_async", side_effect=_test_run_async)


def _mock_httpx_get(status_code: int = 200, json_data: dict | None = None, side_effect=None):
    """יוצר מוק ל-httpx.AsyncClient.get עם תשובה מוגדרת."""
    mock_client = AsyncMock()

    if side_effect:
        mock_client.get = AsyncMock(side_effect=side_effect)
    else:
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = json_data or {}
        mock_client.get = AsyncMock(return_value=mock_response)

    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ============================================================================
# Gateway מחובר
# ============================================================================


class TestWhatsAppConnectionCheckConnected:
    """בדיקות כש-Gateway מחובר ותקין."""

    @pytest.mark.asyncio
    async def test_connected_returns_status_connected(self, fake_redis) -> None:
        """כש-Gateway מחובר — מחזיר status=connected."""
        from app.workers.tasks import check_whatsapp_connection

        mock_client = _mock_httpx_get(
            status_code=200,
            json_data={"status": "ok", "connected": True},
        )

        with _patch_run_async():
            with patch("httpx.AsyncClient", return_value=mock_client):
                with patch(
                    "app.core.redis_client.get_redis",
                    new_callable=AsyncMock,
                    return_value=fake_redis,
                ):
                    result = check_whatsapp_connection()

        assert result["status"] == "connected"
        assert result["alert_sent"] is False

    @pytest.mark.asyncio
    async def test_connected_clears_throttle_key(self, fake_redis) -> None:
        """כש-Gateway מחובר — מנקה throttle key כדי שהתראה הבאה תשלח מיד."""
        from app.workers.tasks import check_whatsapp_connection

        # מדמה מצב שהיה throttle פעיל
        await fake_redis.set("alert_throttle:whatsapp_disconnected", "1", ex=900)

        mock_client = _mock_httpx_get(
            status_code=200,
            json_data={"status": "ok", "connected": True},
        )

        with _patch_run_async():
            with patch("httpx.AsyncClient", return_value=mock_client):
                with patch(
                    "app.core.redis_client.get_redis",
                    new_callable=AsyncMock,
                    return_value=fake_redis,
                ):
                    result = check_whatsapp_connection()

        assert result["status"] == "connected"
        # ה-throttle key אמור להיות מחוק
        val = await fake_redis.get("alert_throttle:whatsapp_disconnected")
        assert val is None


# ============================================================================
# Gateway מנותק (session disconnected)
# ============================================================================


class TestWhatsAppConnectionCheckDisconnected:
    """בדיקות כש-Gateway פעיל אבל ה-session מנותק."""

    @pytest.mark.asyncio
    async def test_disconnected_returns_status_and_sends_alert(
        self, fake_redis
    ) -> None:
        """כש-session מנותק — מחזיר status=disconnected ושולח התראה."""
        from app.workers.tasks import check_whatsapp_connection

        mock_client = _mock_httpx_get(
            status_code=200,
            json_data={"status": "ok", "connected": False},
        )

        mock_send_tg = AsyncMock(return_value=True)

        with _patch_run_async():
            with patch("httpx.AsyncClient", return_value=mock_client):
                with patch(
                    "app.core.redis_client.get_redis",
                    new_callable=AsyncMock,
                    return_value=fake_redis,
                ):
                    with patch("app.core.config.settings") as mock_settings:
                        mock_settings.WHATSAPP_GATEWAY_URL = "http://test-gateway:3000"
                        mock_settings.TELEGRAM_ADMIN_CHAT_IDS = "admin1,admin2"
                        mock_settings.TELEGRAM_ADMIN_CHAT_ID = "group1"

                        with patch(
                            "app.domain.services.admin_notification_service.AdminNotificationService._send_telegram_message",
                            mock_send_tg,
                        ):
                            result = check_whatsapp_connection()

        assert result["status"] == "disconnected"
        assert result["alert_sent"] is True
        # אמור לשלוח ל-3 יעדים: admin1, admin2, group1
        assert mock_send_tg.call_count == 3

    @pytest.mark.asyncio
    async def test_disconnected_throttle_prevents_duplicate_alert(
        self, fake_redis
    ) -> None:
        """אם כבר נשלחה התראה ב-15 הדקות האחרונות — לא שולח שוב."""
        from app.workers.tasks import check_whatsapp_connection

        # מדמה throttle קיים
        await fake_redis.set("alert_throttle:whatsapp_disconnected", "1", ex=900)

        mock_client = _mock_httpx_get(
            status_code=200,
            json_data={"status": "ok", "connected": False},
        )

        mock_send_tg = AsyncMock(return_value=True)

        with _patch_run_async():
            with patch("httpx.AsyncClient", return_value=mock_client):
                with patch(
                    "app.core.redis_client.get_redis",
                    new_callable=AsyncMock,
                    return_value=fake_redis,
                ):
                    with patch(
                        "app.domain.services.admin_notification_service.AdminNotificationService._send_telegram_message",
                        mock_send_tg,
                    ):
                        result = check_whatsapp_connection()

        assert result["status"] == "disconnected"
        assert result["alert_sent"] is False
        # לא אמור לשלוח הודעה
        mock_send_tg.assert_not_called()


# ============================================================================
# Gateway לא זמין (HTTP error)
# ============================================================================


class TestWhatsAppConnectionCheckGatewayError:
    """בדיקות כש-Gateway מחזיר שגיאת HTTP."""

    @pytest.mark.asyncio
    async def test_gateway_error_returns_status(self, fake_redis) -> None:
        """כש-Gateway מחזיר 500 — מחזיר status=gateway_error."""
        from app.workers.tasks import check_whatsapp_connection

        mock_client = _mock_httpx_get(status_code=500)

        mock_send_tg = AsyncMock(return_value=True)

        with _patch_run_async():
            with patch("httpx.AsyncClient", return_value=mock_client):
                with patch(
                    "app.core.redis_client.get_redis",
                    new_callable=AsyncMock,
                    return_value=fake_redis,
                ):
                    with patch("app.core.config.settings") as mock_settings:
                        mock_settings.WHATSAPP_GATEWAY_URL = "http://test:3000"
                        mock_settings.TELEGRAM_ADMIN_CHAT_IDS = "admin1"
                        mock_settings.TELEGRAM_ADMIN_CHAT_ID = ""

                        with patch(
                            "app.domain.services.admin_notification_service.AdminNotificationService._send_telegram_message",
                            mock_send_tg,
                        ):
                            result = check_whatsapp_connection()

        assert result["status"] == "gateway_error"
        assert result["alert_sent"] is True


# ============================================================================
# Gateway timeout / unreachable
# ============================================================================


class TestWhatsAppConnectionCheckNetworkErrors:
    """בדיקות לשגיאות רשת."""

    @pytest.mark.asyncio
    async def test_timeout_returns_status(self, fake_redis) -> None:
        """timeout בבדיקת חיבור — מחזיר status=timeout."""
        from app.workers.tasks import check_whatsapp_connection

        mock_client = _mock_httpx_get(
            side_effect=httpx.TimeoutException("timeout")
        )

        mock_send_tg = AsyncMock(return_value=True)

        with _patch_run_async():
            with patch("httpx.AsyncClient", return_value=mock_client):
                with patch(
                    "app.core.redis_client.get_redis",
                    new_callable=AsyncMock,
                    return_value=fake_redis,
                ):
                    with patch("app.core.config.settings") as mock_settings:
                        mock_settings.WHATSAPP_GATEWAY_URL = "http://test:3000"
                        mock_settings.TELEGRAM_ADMIN_CHAT_IDS = "admin1"
                        mock_settings.TELEGRAM_ADMIN_CHAT_ID = ""

                        with patch(
                            "app.domain.services.admin_notification_service.AdminNotificationService._send_telegram_message",
                            mock_send_tg,
                        ):
                            result = check_whatsapp_connection()

        assert result["status"] == "timeout"
        assert result["alert_sent"] is True

    @pytest.mark.asyncio
    async def test_connection_error_returns_unreachable(self, fake_redis) -> None:
        """שגיאת חיבור — מחזיר status=unreachable."""
        from app.workers.tasks import check_whatsapp_connection

        mock_client = _mock_httpx_get(
            side_effect=httpx.ConnectError("refused")
        )

        mock_send_tg = AsyncMock(return_value=True)

        with _patch_run_async():
            with patch("httpx.AsyncClient", return_value=mock_client):
                with patch(
                    "app.core.redis_client.get_redis",
                    new_callable=AsyncMock,
                    return_value=fake_redis,
                ):
                    with patch("app.core.config.settings") as mock_settings:
                        mock_settings.WHATSAPP_GATEWAY_URL = "http://test:3000"
                        mock_settings.TELEGRAM_ADMIN_CHAT_IDS = "admin1"
                        mock_settings.TELEGRAM_ADMIN_CHAT_ID = ""

                        with patch(
                            "app.domain.services.admin_notification_service.AdminNotificationService._send_telegram_message",
                            mock_send_tg,
                        ):
                            result = check_whatsapp_connection()

        assert result["status"] == "unreachable"
        assert result["alert_sent"] is True


# ============================================================================
# אין מנהלי Telegram מוגדרים
# ============================================================================


class TestWhatsAppConnectionCheckNoAdmins:
    """בדיקות כשאין מנהלי Telegram מוגדרים."""

    @pytest.mark.asyncio
    async def test_no_admins_configured_alert_not_sent(self, fake_redis) -> None:
        """אם אין מנהלי Telegram — ההתראה לא נשלחת."""
        from app.workers.tasks import check_whatsapp_connection

        mock_client = _mock_httpx_get(
            status_code=200,
            json_data={"status": "ok", "connected": False},
        )

        with _patch_run_async():
            with patch("httpx.AsyncClient", return_value=mock_client):
                with patch(
                    "app.core.redis_client.get_redis",
                    new_callable=AsyncMock,
                    return_value=fake_redis,
                ):
                    with patch("app.core.config.settings") as mock_settings:
                        mock_settings.WHATSAPP_GATEWAY_URL = "http://test:3000"
                        mock_settings.TELEGRAM_ADMIN_CHAT_IDS = ""
                        mock_settings.TELEGRAM_ADMIN_CHAT_ID = ""
                        mock_settings.TELEGRAM_BOT_TOKEN = ""

                        with patch(
                            "app.domain.services.admin_notification_service.AdminNotificationService._send_telegram_message",
                            new_callable=AsyncMock,
                            return_value=False,
                        ):
                            result = check_whatsapp_connection()

        assert result["status"] == "disconnected"
        assert result["alert_sent"] is False


# ============================================================================
# בדיקת beat schedule
# ============================================================================


class TestWhatsAppConnectionBeatSchedule:
    """בדיקות שה-task רשום ב-Celery beat schedule."""

    @pytest.mark.unit
    def test_task_registered_in_beat_schedule(self) -> None:
        """ה-task רשום ב-beat schedule עם מרווח של 3 דקות."""
        from app.workers.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "check-whatsapp-connection-every-3-minutes" in schedule

        entry = schedule["check-whatsapp-connection-every-3-minutes"]
        assert entry["task"] == "app.workers.tasks.check_whatsapp_connection"
        assert entry["schedule"] == 180.0
