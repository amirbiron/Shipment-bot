"""
Structured Logging Infrastructure

Provides JSON-formatted logging with correlation IDs for request tracing.
"""
import logging
import json
import sys
import uuid
from datetime import datetime
from typing import Any
from contextvars import ContextVar
from functools import wraps

# Context variable for correlation ID
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add correlation ID if available
        correlation_id = correlation_id_var.get()
        if correlation_id:
            log_entry["correlation_id"] = correlation_id

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        if hasattr(record, "extra_data"):
            log_entry["extra"] = record.extra_data

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class StructuredLogger(logging.Logger):
    """Extended logger with structured logging support"""

    def _log_with_extra(
        self,
        level: int,
        msg: str,
        args: tuple,
        extra_data: dict[str, Any] | None = None,
        **kwargs
    ) -> None:
        if extra_data:
            extra = kwargs.get("extra", {})
            extra["extra_data"] = extra_data
            kwargs["extra"] = extra
        super()._log(level, msg, args, **kwargs)

    def debug(self, msg: str, *args, extra_data: dict[str, Any] | None = None, **kwargs) -> None:
        if self.isEnabledFor(logging.DEBUG):
            self._log_with_extra(logging.DEBUG, msg, args, extra_data, **kwargs)

    def info(self, msg: str, *args, extra_data: dict[str, Any] | None = None, **kwargs) -> None:
        if self.isEnabledFor(logging.INFO):
            self._log_with_extra(logging.INFO, msg, args, extra_data, **kwargs)

    def warning(self, msg: str, *args, extra_data: dict[str, Any] | None = None, **kwargs) -> None:
        if self.isEnabledFor(logging.WARNING):
            self._log_with_extra(logging.WARNING, msg, args, extra_data, **kwargs)

    def error(self, msg: str, *args, extra_data: dict[str, Any] | None = None, **kwargs) -> None:
        if self.isEnabledFor(logging.ERROR):
            self._log_with_extra(logging.ERROR, msg, args, extra_data, **kwargs)

    def critical(self, msg: str, *args, extra_data: dict[str, Any] | None = None, **kwargs) -> None:
        if self.isEnabledFor(logging.CRITICAL):
            self._log_with_extra(logging.CRITICAL, msg, args, extra_data, **kwargs)


# Set custom logger class
logging.setLoggerClass(StructuredLogger)


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    app_name: str = "shipment-bot"
) -> None:
    """
    Configure application logging.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Use JSON formatting for production
        app_name: Application name for log identification
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))

    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        # Human-readable format for development
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | [%(correlation_id)s] | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        # Add filter to inject correlation_id
        handler.addFilter(CorrelationIdFilter())

    root_logger.addHandler(handler)

    # Set levels for third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.INFO)


class CorrelationIdFilter(logging.Filter):
    """Filter that adds correlation_id to log records"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get() or "-"
        return True


def generate_correlation_id() -> str:
    """Generate a new correlation ID"""
    return str(uuid.uuid4())[:8]


def set_correlation_id(correlation_id: str | None = None) -> str:
    """Set correlation ID for current context"""
    cid = correlation_id or generate_correlation_id()
    correlation_id_var.set(cid)
    return cid


def get_correlation_id() -> str:
    """Get current correlation ID, generating and persisting one if not set"""
    cid = correlation_id_var.get()
    if not cid:
        # יצירת ID חדש ושמירתו לשימוש עתידי באותו context
        cid = generate_correlation_id()
        correlation_id_var.set(cid)
    return cid


def get_logger(name: str) -> StructuredLogger:
    """Get a structured logger instance"""
    return logging.getLogger(name)  # type: ignore


def log_async_operation(operation_name: str):
    """Decorator for logging async operations with timing"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            logger = get_logger(func.__module__)
            start_time = datetime.utcnow()

            logger.debug(
                f"Starting {operation_name}",
                extra_data={"operation": operation_name, "status": "started"}
            )

            try:
                result = await func(*args, **kwargs)
                duration = (datetime.utcnow() - start_time).total_seconds()

                logger.info(
                    f"Completed {operation_name}",
                    extra_data={
                        "operation": operation_name,
                        "status": "completed",
                        "duration_seconds": duration
                    }
                )
                return result
            except Exception as e:
                duration = (datetime.utcnow() - start_time).total_seconds()

                logger.error(
                    f"Failed {operation_name}: {str(e)}",
                    extra_data={
                        "operation": operation_name,
                        "status": "failed",
                        "duration_seconds": duration,
                        "error": str(e)
                    },
                    exc_info=True
                )
                raise

        return wrapper
    return decorator


def log_sync_operation(operation_name: str):
    """Decorator for logging sync operations with timing"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger(func.__module__)
            start_time = datetime.utcnow()

            logger.debug(
                f"Starting {operation_name}",
                extra_data={"operation": operation_name, "status": "started"}
            )

            try:
                result = func(*args, **kwargs)
                duration = (datetime.utcnow() - start_time).total_seconds()

                logger.info(
                    f"Completed {operation_name}",
                    extra_data={
                        "operation": operation_name,
                        "status": "completed",
                        "duration_seconds": duration
                    }
                )
                return result
            except Exception as e:
                duration = (datetime.utcnow() - start_time).total_seconds()

                logger.error(
                    f"Failed {operation_name}: {str(e)}",
                    extra_data={
                        "operation": operation_name,
                        "status": "failed",
                        "duration_seconds": duration,
                        "error": str(e)
                    },
                    exc_info=True
                )
                raise

        return wrapper
    return decorator
