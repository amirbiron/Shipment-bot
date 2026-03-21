"""
בדיקות למודול Sentry — אתחול, סינון PII, ו-traces sampler
"""
import pytest
from unittest.mock import patch, MagicMock

from app.core.sentry import (
    _scrub_phones,
    _scrub_dict,
    _before_send,
    _before_send_transaction,
    _traces_sampler,
    init_sentry,
    set_sentry_user,
    capture_message,
)


class TestScrubPhones:
    """בדיקות סינון מספרי טלפון מטקסט"""

    @pytest.mark.unit
    def test_scrub_israeli_mobile(self) -> None:
        text = "שליח: 0501234567 הגיע"
        result = _scrub_phones(text)
        assert "0501234567" not in result
        assert "[REDACTED_PHONE]" in result

    @pytest.mark.unit
    def test_scrub_international_format(self) -> None:
        text = "התקשרו ל-+972501234567"
        result = _scrub_phones(text)
        assert "+972501234567" not in result
        assert "[REDACTED_PHONE]" in result

    @pytest.mark.unit
    def test_scrub_with_dashes(self) -> None:
        text = "טלפון: 050-123-4567"
        result = _scrub_phones(text)
        assert "050-123-4567" not in result
        assert "[REDACTED_PHONE]" in result

    @pytest.mark.unit
    def test_no_phone_unchanged(self) -> None:
        text = "משלוח מספר 42 הגיע"
        result = _scrub_phones(text)
        assert result == text

    @pytest.mark.unit
    def test_multiple_phones(self) -> None:
        text = "שולח: 0521234567, מקבל: 0539876543"
        result = _scrub_phones(text)
        assert "0521234567" not in result
        assert "0539876543" not in result
        assert result.count("[REDACTED_PHONE]") == 2


class TestScrubDict:
    """בדיקות סינון מספרי טלפון ממילון"""

    @pytest.mark.unit
    def test_scrub_string_values(self) -> None:
        data = {"phone": "0501234567", "name": "test"}
        result = _scrub_dict(data)
        assert "[REDACTED_PHONE]" in result["phone"]
        assert result["name"] == "test"

    @pytest.mark.unit
    def test_scrub_nested_dict(self) -> None:
        data = {"user": {"phone": "0501234567"}}
        result = _scrub_dict(data)
        assert "[REDACTED_PHONE]" in result["user"]["phone"]

    @pytest.mark.unit
    def test_scrub_list_values(self) -> None:
        data = {"phones": ["0501234567", "0529876543"]}
        result = _scrub_dict(data)
        assert all("[REDACTED_PHONE]" in p for p in result["phones"])

    @pytest.mark.unit
    def test_non_string_values_preserved(self) -> None:
        data = {"count": 42, "active": True, "rate": 3.14}
        result = _scrub_dict(data)
        assert result == data


class TestBeforeSend:
    """בדיקות ל-before_send callback"""

    @pytest.mark.unit
    def test_adds_correlation_id_tag(self) -> None:
        with patch("app.core.sentry.get_correlation_id", return_value="abc123"):
            event = _before_send({}, {})
        assert event["tags"]["correlation_id"] == "abc123"

    @pytest.mark.unit
    def test_scrubs_exception_value(self) -> None:
        event = {
            "exception": {
                "values": [{"value": "שגיאה עבור 0501234567", "type": "ValueError"}]
            }
        }
        with patch("app.core.sentry.get_correlation_id", return_value=""):
            result = _before_send(event, {})
        assert "0501234567" not in result["exception"]["values"][0]["value"]
        assert "[REDACTED_PHONE]" in result["exception"]["values"][0]["value"]

    @pytest.mark.unit
    def test_scrubs_breadcrumb_message(self) -> None:
        event = {
            "breadcrumbs": {
                "values": [{"message": "שליחה ל-0501234567"}]
            }
        }
        with patch("app.core.sentry.get_correlation_id", return_value=""):
            result = _before_send(event, {})
        assert "0501234567" not in result["breadcrumbs"]["values"][0]["message"]

    @pytest.mark.unit
    def test_scrubs_request_data(self) -> None:
        event = {
            "request": {
                "data": {"phone": "0501234567"},
                "headers": {"X-User": "0501234567"},
            }
        }
        with patch("app.core.sentry.get_correlation_id", return_value=""):
            result = _before_send(event, {})
        assert "0501234567" not in result["request"]["data"]["phone"]
        assert "0501234567" not in result["request"]["headers"]["X-User"]

    @pytest.mark.unit
    def test_scrubs_message(self) -> None:
        event = {"message": "שגיאה בשליחה ל-0501234567"}
        with patch("app.core.sentry.get_correlation_id", return_value=""):
            result = _before_send(event, {})
        assert "0501234567" not in result["message"]


class TestBeforeSendTransaction:
    """בדיקות ל-before_send_transaction callback"""

    @pytest.mark.unit
    def test_scrubs_transaction_name(self) -> None:
        event = {"transaction": "POST /api/users/0501234567/deliver"}
        result = _before_send_transaction(event, {})
        assert "0501234567" not in result["transaction"]


class TestTracesSampler:
    """בדיקות ל-traces sampler — סינון health checks"""

    @pytest.mark.unit
    def test_health_check_not_sampled(self) -> None:
        ctx = {"transaction_context": {"name": "GET /health"}}
        assert _traces_sampler(ctx) == 0.0

    @pytest.mark.unit
    def test_health_ready_not_sampled(self) -> None:
        ctx = {"transaction_context": {"name": "GET /health/ready"}}
        assert _traces_sampler(ctx) == 0.0

    @pytest.mark.unit
    def test_regular_endpoint_sampled(self) -> None:
        ctx = {"transaction_context": {"name": "POST /api/deliveries"}}
        rate = _traces_sampler(ctx)
        assert rate > 0.0


class TestInitSentry:
    """בדיקות לפונקציית אתחול"""

    @pytest.mark.unit
    def test_init_skipped_when_no_dsn(self) -> None:
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.SENTRY_DSN = ""
            with patch("sentry_sdk.init") as mock_init:
                init_sentry()
                mock_init.assert_not_called()

    @pytest.mark.unit
    def test_init_called_with_dsn(self) -> None:
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.SENTRY_DSN = "https://key@sentry.io/123"
            mock_settings.SENTRY_ENVIRONMENT = "test"
            mock_settings.SENTRY_TRACES_SAMPLE_RATE = 0.5
            mock_settings.SENTRY_PROFILES_SAMPLE_RATE = 0.1
            with patch("sentry_sdk.init") as mock_init:
                init_sentry()
                mock_init.assert_called_once()
                call_kwargs = mock_init.call_args[1]
                assert call_kwargs["dsn"] == "https://key@sentry.io/123"
                assert call_kwargs["environment"] == "test"
                assert call_kwargs["send_default_pii"] is False


class TestSentryHelpers:
    """בדיקות לפונקציות עזר"""

    @pytest.mark.unit
    def test_set_sentry_user(self) -> None:
        with patch("sentry_sdk.set_user") as mock_set_user:
            set_sentry_user(42, "courier")
            mock_set_user.assert_called_once_with({"id": "42", "role": "courier"})

    @pytest.mark.unit
    def test_set_sentry_user_default_role(self) -> None:
        with patch("sentry_sdk.set_user") as mock_set_user:
            set_sentry_user(42)
            mock_set_user.assert_called_once_with({"id": "42", "role": "unknown"})

    @pytest.mark.unit
    def test_capture_message(self) -> None:
        with patch("sentry_sdk.capture_message") as mock_capture:
            capture_message("בדיקה", level="warning")
            mock_capture.assert_called_once_with("בדיקה", level="warning")
