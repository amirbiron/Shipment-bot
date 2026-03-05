"""
FastAPI Middleware

Provides request/response middleware for:
- Correlation ID injection
- Request logging (with PII masking)
- Global error handling
- Security headers (HSTS, CSP upgrade-insecure-requests)
- Rate limiting for webhook endpoints
"""
import re
import time
from collections import defaultdict
from typing import Callable
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import (
    get_logger,
    set_correlation_id,
    get_correlation_id
)
from app.core.exceptions import AppException

logger = get_logger(__name__)

# ביטוי רגולרי לזיהוי מספרי טלפון ב-URL path (ישראלי/בינלאומי)
_PHONE_IN_PATH_RE = re.compile(r"(\+?\d{3})\d{4}(\d{3})")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware to add correlation ID to requests"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable
    ) -> Response:
        # Get correlation ID from header or generate new one
        correlation_id = request.headers.get("X-Correlation-ID")
        correlation_id = set_correlation_id(correlation_id)

        # Add to request state for access in handlers
        request.state.correlation_id = correlation_id

        # Process request
        response = await call_next(request)

        # Add correlation ID to response headers
        response.headers["X-Correlation-ID"] = correlation_id

        return response


def _mask_path_pii(path: str) -> str:
    """מיסוך מספרי טלפון ב-URL path לפרטיות — מחליף 4 ספרות אמצעיות ב-****"""
    return _PHONE_IN_PATH_RE.sub(r"\1****\2", path)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log requests and responses (with PII masking)"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable
    ) -> Response:
        start_time = time.time()
        safe_path = _mask_path_pii(request.url.path)

        # Log request
        logger.info(
            f"Request started: {request.method} {safe_path}",
            extra_data={
                "method": request.method,
                "path": safe_path,
                "query_params": dict(request.query_params),
                "client_host": request.client.host if request.client else None,
            }
        )

        try:
            response = await call_next(request)
            duration = time.time() - start_time

            # Log response
            log_level = "info" if response.status_code < 400 else "warning"
            getattr(logger, log_level)(
                f"Request completed: {request.method} {safe_path}",
                extra_data={
                    "method": request.method,
                    "path": safe_path,
                    "status_code": response.status_code,
                    "duration_seconds": round(duration, 4),
                }
            )

            return response
        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"Request failed: {request.method} {safe_path}",
                extra_data={
                    "method": request.method,
                    "path": safe_path,
                    "duration_seconds": round(duration, 4),
                    "error": str(e),
                },
                exc_info=True
            )
            raise


async def app_exception_handler(
    request: Request,
    exc: AppException
) -> JSONResponse:
    """Handle application exceptions"""
    logger.warning(
        f"Application exception: {exc.error_code.value}",
        extra_data={
            "error_code": exc.error_code.value,
            "message": exc.message,
            "details": exc.details,
            "path": request.url.path,
        }
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
        headers={"X-Correlation-ID": get_correlation_id()}
    )


async def generic_exception_handler(
    request: Request,
    exc: Exception
) -> JSONResponse:
    """Handle unexpected exceptions"""
    logger.error(
        f"Unhandled exception: {type(exc).__name__}",
        extra_data={
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "path": request.url.path,
        },
        exc_info=True
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "ERR_1000",
                "message": "An unexpected error occurred",
                "details": {}
            }
        },
        headers={"X-Correlation-ID": get_correlation_id()}
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware להוספת כותרות אבטחה לכל תשובה.

    - Content-Security-Policy: upgrade-insecure-requests — מונע אזהרות mixed content
      ע"י הנחיית הדפדפן לשדרג אוטומטית בקשות HTTP ל-HTTPS.
    - Strict-Transport-Security (HSTS) — מחייב את הדפדפן לגשת רק ב-HTTPS.
    - X-Content-Type-Options: nosniff — מונע MIME sniffing.

    הערה: הכותרות מוחלות רק כשאפליקציה לא במצב DEBUG, כדי לא לחסום פיתוח מקומי ב-HTTP.
    """

    def __init__(self, app: FastAPI, *, debug: bool = False) -> None:
        super().__init__(app)
        self._debug = debug

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        response = await call_next(request)

        # X-Content-Type-Options בטוח תמיד — מונע MIME sniffing גם בפיתוח
        response.headers["X-Content-Type-Options"] = "nosniff"

        if not self._debug:
            response.headers["Content-Security-Policy"] = "upgrade-insecure-requests"
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        return response


class WebhookRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting לנקודות webhook — sliding window לפי IP.

    מגביל מספר בקשות לחלון זמן נתון (ברירת מחדל: 100 בקשות / 60 שניות)
    על paths שמכילים /webhook. מחזיר 429 Too Many Requests אם חורג.

    הערה: ממוקם בתוך CorrelationIdMiddleware ב-stack, כך שלכל בקשה
    (כולל 429) יש correlation ID תקין בלוגים וב-response.
    """

    def __init__(
        self,
        app: FastAPI,
        *,
        max_requests: int = 100,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        # מיפוי IP → רשימת timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _cleanup_window(self, ip: str, now: float) -> None:
        """ניקוי בקשות ישנות מחוץ לחלון הזמן + מחיקת IP ריקים"""
        cutoff = now - self._window_seconds
        timestamps = self._requests[ip]
        # מחפשים את האינדקס הראשון בתוך החלון
        idx = 0
        for idx, ts in enumerate(timestamps):
            if ts >= cutoff:
                break
        else:
            idx = len(timestamps)
        if idx > 0:
            self._requests[ip] = timestamps[idx:]
        # מחיקת מפתח ריק — מונע דליפת זיכרון מ-IP חד-פעמיים
        if not self._requests[ip]:
            del self._requests[ip]

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        # הגבלה רק על paths של webhooks
        path = request.url.path
        if "/webhook" not in path:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        self._cleanup_window(client_ip, now)

        if len(self._requests.get(client_ip, [])) >= self._max_requests:
            logger.warning(
                "Rate limit exceeded for webhook",
                extra_data={
                    "client_ip": client_ip,
                    "path": path,
                    "limit": self._max_requests,
                    "window_seconds": self._window_seconds,
                },
            )
            return JSONResponse(
                status_code=429,
                content={"error": "Too many requests. Please try again later."},
                headers={
                    "Retry-After": str(self._window_seconds),
                    "X-Correlation-ID": get_correlation_id(),
                },
            )

        self._requests[client_ip].append(now)
        return await call_next(request)


def setup_middleware(app: FastAPI) -> None:
    """Setup all middleware for the application"""
    from app.core.config import settings

    # ב-Starlette, ה-middleware האחרון שנוסף הוא ה-outermost (עוטף את כולם).
    # סדר הוספה (מלמטה ל-outermost):
    #   1. RateLimit (innermost — רץ אחרי CorrelationId כדי שיש correlation ID ב-429)
    #   2. RequestLogging (מקבל את התשובה כולל 429 + correlation ID)
    #   3. CorrelationId (מגדיר correlation ID לפני הכל)
    #   4. SecurityHeaders (outermost — מוסיף headers לכל תשובה)
    #
    # סדר עיבוד בקשה: SecurityHeaders → CorrelationId → RequestLogging → RateLimit → app
    app.add_middleware(
        WebhookRateLimitMiddleware,
        max_requests=settings.WEBHOOK_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.WEBHOOK_RATE_LIMIT_WINDOW_SECONDS,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, debug=settings.DEBUG)

    # Register exception handlers
    app.add_exception_handler(AppException, app_exception_handler)


def setup_exception_handlers(app: FastAPI) -> None:
    """Setup exception handlers"""
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
