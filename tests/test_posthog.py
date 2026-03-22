"""
בדיקות למודול PostHog — אתחול, שליחת אירועים, זיהוי משתמשים ומיסוך PII
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.core.posthog import (
    _scrub_properties,
    init_posthog,
    capture_event,
    identify_user,
    shutdown_posthog,
)
import app.core.posthog as posthog_module


class TestScrubProperties:
    """בדיקות מיסוך PII בנתוני אירועים"""

    @pytest.mark.unit
    def test_scrub_phone_in_string_value(self) -> None:
        props = {"phone": "0501234567", "action": "register"}
        result = _scrub_properties(props)
        assert "0501234567" not in result["phone"]
        assert "[REDACTED_PHONE]" in result["phone"]
        assert result["action"] == "register"

    @pytest.mark.unit
    def test_scrub_phone_in_nested_dict(self) -> None:
        props = {"user": {"phone": "+972501234567", "name": "test"}}
        result = _scrub_properties(props)
        assert "+972501234567" not in str(result)
        assert result["user"]["name"] == "test"

    @pytest.mark.unit
    def test_no_phone_unchanged(self) -> None:
        props = {"delivery_id": 42, "status": "delivered"}
        result = _scrub_properties(props)
        assert result == props

    @pytest.mark.unit
    def test_scrub_phone_in_list_value(self) -> None:
        props = {"recipients": ["0501234567", "some text"]}
        result = _scrub_properties(props)
        assert "0501234567" not in str(result)
        assert result["recipients"][1] == "some text"

    @pytest.mark.unit
    def test_numeric_values_preserved(self) -> None:
        props = {"count": 5, "fee": 10.0, "active": True}
        result = _scrub_properties(props)
        assert result == props


class TestInitPosthog:
    """בדיקות אתחול PostHog"""

    @pytest.mark.unit
    def test_init_disabled_when_no_api_key(self) -> None:
        """PostHog לא מאותחל כש-API Key ריק"""
        with patch("app.core.posthog.logger") as mock_logger:
            _settings = MagicMock()
            _settings.POSTHOG_PROJECT_TOKEN = ""
            with patch("app.core.config.settings", _settings):
                init_posthog()
                mock_logger.info.assert_called_once()
                assert "מושבת" in mock_logger.info.call_args[0][0]

    @pytest.mark.unit
    def test_init_success_with_api_key(self) -> None:
        """PostHog מאותחל בהצלחה כש-API Key מוגדר"""
        _settings = MagicMock()
        _settings.POSTHOG_PROJECT_TOKEN = "phc_test_key"
        _settings.POSTHOG_HOST = "https://us.i.posthog.com"
        _settings.DEBUG = False

        mock_posthog_class = MagicMock()
        mock_instance = MagicMock()
        mock_posthog_class.return_value = mock_instance

        with patch("app.core.config.settings", _settings), \
             patch.dict("sys.modules", {"posthog": MagicMock(Posthog=mock_posthog_class)}), \
             patch("app.core.posthog.logger") as mock_logger:
            # ניקוי client קודם
            posthog_module._posthog_client = None
            init_posthog()
            assert posthog_module._posthog_client is not None
            mock_logger.info.assert_called()

        # ניקוי אחרי הבדיקה
        posthog_module._posthog_client = None


    @pytest.mark.unit
    def test_init_idempotent_does_not_create_second_client(self) -> None:
        """קריאה חוזרת ל-init_posthog לא יוצרת client כפול"""
        existing_client = MagicMock()
        posthog_module._posthog_client = existing_client

        try:
            init_posthog()
            # ה-client לא השתנה — לא נוצר חדש
            assert posthog_module._posthog_client is existing_client
        finally:
            posthog_module._posthog_client = None


class TestCaptureEvent:
    """בדיקות שליחת אירועים"""

    @pytest.mark.unit
    def test_capture_when_disabled(self) -> None:
        """לא קורס כש-PostHog מושבת"""
        posthog_module._posthog_client = None
        # לא אמור לזרוק exception
        capture_event("user_1", "test_event", {"key": "value"})

    @pytest.mark.unit
    def test_capture_calls_client(self) -> None:
        """אירוע נשלח ל-PostHog client"""
        mock_client = MagicMock()
        posthog_module._posthog_client = mock_client

        try:
            capture_event("user_1", "delivery_created", {"delivery_id": 42})
            mock_client.capture.assert_called_once_with(
                distinct_id="user_1",
                event="delivery_created",
                properties={"delivery_id": 42},
            )
        finally:
            posthog_module._posthog_client = None

    @pytest.mark.unit
    def test_capture_scrubs_phone(self) -> None:
        """מספרי טלפון מוסתרים באירועים"""
        mock_client = MagicMock()
        posthog_module._posthog_client = mock_client

        try:
            capture_event("user_1", "message_sent", {"phone": "0501234567"})
            call_props = mock_client.capture.call_args[1]["properties"]
            assert "0501234567" not in call_props["phone"]
        finally:
            posthog_module._posthog_client = None

    @pytest.mark.unit
    def test_capture_handles_exception(self) -> None:
        """שגיאה ב-capture לא מפילה את האפליקציה"""
        mock_client = MagicMock()
        mock_client.capture.side_effect = Exception("network error")
        posthog_module._posthog_client = mock_client

        try:
            # לא אמור לזרוק exception
            capture_event("user_1", "test_event")
        finally:
            posthog_module._posthog_client = None


class TestIdentifyUser:
    """בדיקות זיהוי משתמשים"""

    @pytest.mark.unit
    def test_identify_when_disabled(self) -> None:
        """לא קורס כש-PostHog מושבת"""
        posthog_module._posthog_client = None
        identify_user("user_1", {"role": "courier"})

    @pytest.mark.unit
    def test_identify_calls_client(self) -> None:
        """זיהוי נשלח ל-PostHog client"""
        mock_client = MagicMock()
        posthog_module._posthog_client = mock_client

        try:
            identify_user("user_1", {"role": "courier", "platform": "telegram"})
            mock_client.identify.assert_called_once_with(
                distinct_id="user_1",
                properties={"role": "courier", "platform": "telegram"},
            )
        finally:
            posthog_module._posthog_client = None


class TestShutdownPosthog:
    """בדיקות סגירת PostHog"""

    @pytest.mark.unit
    def test_shutdown_when_disabled(self) -> None:
        """לא קורס כש-PostHog מושבת"""
        posthog_module._posthog_client = None
        shutdown_posthog()

    @pytest.mark.unit
    def test_shutdown_calls_client(self) -> None:
        """shutdown נקרא על ה-client"""
        mock_client = MagicMock()
        posthog_module._posthog_client = mock_client

        shutdown_posthog()
        mock_client.shutdown.assert_called_once()
        assert posthog_module._posthog_client is None

    @pytest.mark.unit
    def test_shutdown_handles_exception(self) -> None:
        """שגיאה ב-shutdown לא מפילה את האפליקציה"""
        mock_client = MagicMock()
        mock_client.shutdown.side_effect = Exception("error")
        posthog_module._posthog_client = mock_client

        shutdown_posthog()
        assert posthog_module._posthog_client is None
