"""
Tests for Logging Infrastructure
"""
import pytest
import json
import logging
from io import StringIO

from app.core.logging import (
    setup_logging,
    get_logger,
    set_correlation_id,
    get_correlation_id,
    generate_correlation_id,
    JSONFormatter,
    log_async_operation
)


class TestCorrelationId:
    """Tests for correlation ID management"""

    @pytest.mark.unit
    def test_generate_correlation_id(self):
        """Test correlation ID generation"""
        cid = generate_correlation_id()

        assert cid is not None
        assert len(cid) == 8
        assert cid.isalnum()

    @pytest.mark.unit
    def test_set_and_get_correlation_id(self):
        """Test setting and getting correlation ID"""
        test_id = "test1234"
        result = set_correlation_id(test_id)

        assert result == test_id
        assert get_correlation_id() == test_id

    @pytest.mark.unit
    def test_set_correlation_id_generates_if_none(self):
        """Test that set_correlation_id generates ID if none provided"""
        result = set_correlation_id(None)

        assert result is not None
        assert len(result) == 8


class TestJSONFormatter:
    """Tests for JSON log formatting"""

    @pytest.fixture
    def log_stream(self) -> StringIO:
        """Create a string stream for capturing logs"""
        return StringIO()

    @pytest.fixture
    def json_handler(self, log_stream: StringIO) -> logging.Handler:
        """Create a handler with JSON formatter"""
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(JSONFormatter())
        return handler

    @pytest.mark.unit
    def test_json_format_basic(self, log_stream: StringIO, json_handler: logging.Handler):
        """Test basic JSON log formatting"""
        logger = logging.getLogger("test_json_basic")
        logger.addHandler(json_handler)
        logger.setLevel(logging.INFO)

        logger.info("Test message")

        log_output = log_stream.getvalue()
        log_entry = json.loads(log_output)

        assert log_entry["level"] == "INFO"
        assert log_entry["message"] == "Test message"
        assert "timestamp" in log_entry
        assert log_entry["logger"] == "test_json_basic"

    @pytest.mark.unit
    def test_json_format_with_correlation_id(
        self,
        log_stream: StringIO,
        json_handler: logging.Handler
    ):
        """Test JSON formatting includes correlation ID"""
        set_correlation_id("testcorr")

        logger = logging.getLogger("test_json_corr")
        logger.addHandler(json_handler)
        logger.setLevel(logging.INFO)

        logger.info("Correlated message")

        log_output = log_stream.getvalue()
        log_entry = json.loads(log_output)

        assert log_entry.get("correlation_id") == "testcorr"

    @pytest.mark.unit
    def test_json_format_with_exception(
        self,
        log_stream: StringIO,
        json_handler: logging.Handler
    ):
        """Test JSON formatting includes exception info"""
        logger = logging.getLogger("test_json_exc")
        logger.addHandler(json_handler)
        logger.setLevel(logging.ERROR)

        try:
            raise ValueError("Test error")
        except ValueError:
            logger.error("Error occurred", exc_info=True)

        log_output = log_stream.getvalue()
        log_entry = json.loads(log_output)

        assert log_entry["level"] == "ERROR"
        assert "exception" in log_entry
        assert "ValueError" in log_entry["exception"]


class TestStructuredLogger:
    """Tests for structured logger functionality"""

    @pytest.mark.unit
    def test_get_logger(self):
        """Test getting a logger instance"""
        logger = get_logger("test.module")

        assert logger is not None
        assert logger.name == "test.module"

    @pytest.mark.unit
    def test_logger_with_extra_data(self):
        """Test logging with extra data"""
        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(JSONFormatter())

        logger = get_logger("test.extra")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Note: extra_data is our custom parameter
        logger.info("Message with data", extra_data={"user_id": 123, "action": "test"})

        log_output = log_stream.getvalue()
        log_entry = json.loads(log_output)

        assert log_entry["extra"]["user_id"] == 123
        assert log_entry["extra"]["action"] == "test"


class TestAsyncLoggingDecorator:
    """Tests for async operation logging decorator"""

    @pytest.mark.unit
    async def test_log_async_operation_success(self):
        """Test async operation logging on success"""
        @log_async_operation("test_operation")
        async def success_func():
            return "success"

        result = await success_func()
        assert result == "success"

    @pytest.mark.unit
    async def test_log_async_operation_failure(self):
        """Test async operation logging on failure"""
        @log_async_operation("failing_operation")
        async def failing_func():
            raise ValueError("Test failure")

        with pytest.raises(ValueError):
            await failing_func()
