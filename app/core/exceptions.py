"""
Custom Exception Hierarchy

Provides structured exceptions for consistent error handling across the application.
"""
from typing import Any
from enum import Enum


class ErrorCode(str, Enum):
    """Standard error codes for API responses"""

    # General errors (1xxx)
    INTERNAL_ERROR = "ERR_1000"
    VALIDATION_ERROR = "ERR_1001"
    NOT_FOUND = "ERR_1002"
    ALREADY_EXISTS = "ERR_1003"
    UNAUTHORIZED = "ERR_1004"
    FORBIDDEN = "ERR_1005"
    RATE_LIMITED = "ERR_1006"

    # Delivery errors (2xxx)
    DELIVERY_NOT_FOUND = "ERR_2001"
    DELIVERY_ALREADY_CAPTURED = "ERR_2002"
    DELIVERY_ALREADY_DELIVERED = "ERR_2003"
    DELIVERY_CANNOT_CANCEL = "ERR_2004"
    DELIVERY_INVALID_STATUS = "ERR_2005"

    # User errors (3xxx)
    USER_NOT_FOUND = "ERR_3001"
    USER_NOT_APPROVED = "ERR_3002"
    USER_ALREADY_EXISTS = "ERR_3003"
    INVALID_USER_ROLE = "ERR_3004"

    # Wallet errors (4xxx)
    WALLET_NOT_FOUND = "ERR_4001"
    INSUFFICIENT_CREDIT = "ERR_4002"
    INVALID_AMOUNT = "ERR_4003"
    WALLET_LOCKED = "ERR_4004"

    # External service errors (5xxx)
    TELEGRAM_ERROR = "ERR_5001"
    WHATSAPP_ERROR = "ERR_5002"
    EXTERNAL_SERVICE_UNAVAILABLE = "ERR_5003"
    EXTERNAL_SERVICE_TIMEOUT = "ERR_5004"

    # State machine errors (6xxx)
    INVALID_STATE_TRANSITION = "ERR_6001"
    SESSION_NOT_FOUND = "ERR_6002"
    INVALID_STATE = "ERR_6003"


class AppException(Exception):
    """Base exception for all application errors"""

    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        status_code: int = 500,
        details: dict[str, Any] | None = None
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for API response"""
        return {
            "error": {
                "code": self.error_code.value,
                "message": self.message,
                "details": self.details
            }
        }


class ValidationException(AppException):
    """Raised when input validation fails"""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            details=details
        )
        if field:
            self.details["field"] = field


class NotFoundException(AppException):
    """Raised when a requested resource is not found"""

    def __init__(
        self,
        resource: str,
        identifier: Any,
        error_code: ErrorCode = ErrorCode.NOT_FOUND
    ):
        super().__init__(
            message=f"{resource} not found: {identifier}",
            error_code=error_code,
            status_code=404,
            details={"resource": resource, "identifier": str(identifier)}
        )


class DeliveryException(AppException):
    """Base exception for delivery-related errors"""

    def __init__(
        self,
        message: str,
        error_code: ErrorCode,
        delivery_id: int | None = None,
        details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=400,
            details=details
        )
        if delivery_id:
            self.details["delivery_id"] = delivery_id


class DeliveryNotFoundError(DeliveryException):
    """Raised when delivery is not found"""

    def __init__(self, delivery_id: int):
        super().__init__(
            message=f"Delivery not found: {delivery_id}",
            error_code=ErrorCode.DELIVERY_NOT_FOUND,
            delivery_id=delivery_id
        )
        self.status_code = 404


class DeliveryAlreadyCapturedError(DeliveryException):
    """Raised when trying to capture an already captured delivery"""

    def __init__(self, delivery_id: int, courier_id: int | None = None):
        super().__init__(
            message=f"Delivery {delivery_id} has already been captured",
            error_code=ErrorCode.DELIVERY_ALREADY_CAPTURED,
            delivery_id=delivery_id,
            details={"current_courier_id": courier_id} if courier_id else None
        )


class DeliveryStatusError(DeliveryException):
    """Raised when delivery has invalid status for operation"""

    def __init__(self, delivery_id: int, current_status: str, required_status: str):
        super().__init__(
            message=f"Delivery {delivery_id} has status '{current_status}', required '{required_status}'",
            error_code=ErrorCode.DELIVERY_INVALID_STATUS,
            delivery_id=delivery_id,
            details={"current_status": current_status, "required_status": required_status}
        )


class UserException(AppException):
    """Base exception for user-related errors"""

    def __init__(
        self,
        message: str,
        error_code: ErrorCode,
        user_id: int | None = None,
        details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=400,
            details=details
        )
        if user_id:
            self.details["user_id"] = user_id


class UserNotFoundError(UserException):
    """Raised when user is not found"""

    def __init__(self, identifier: str | int):
        super().__init__(
            message=f"User not found: {identifier}",
            error_code=ErrorCode.USER_NOT_FOUND,
            user_id=identifier if isinstance(identifier, int) else None
        )
        self.status_code = 404
        if isinstance(identifier, str):
            self.details["identifier"] = identifier


class UserNotApprovedError(UserException):
    """Raised when user is not approved for action"""

    def __init__(self, user_id: int):
        super().__init__(
            message=f"User {user_id} is not approved",
            error_code=ErrorCode.USER_NOT_APPROVED,
            user_id=user_id
        )


class WalletException(AppException):
    """Base exception for wallet-related errors"""

    def __init__(
        self,
        message: str,
        error_code: ErrorCode,
        courier_id: int | None = None,
        details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=400,
            details=details
        )
        if courier_id:
            self.details["courier_id"] = courier_id


class InsufficientCreditError(WalletException):
    """Raised when courier doesn't have enough credit"""

    def __init__(
        self,
        courier_id: int,
        current_balance: float,
        required_amount: float,
        credit_limit: float
    ):
        super().__init__(
            message=f"Insufficient credit for courier {courier_id}",
            error_code=ErrorCode.INSUFFICIENT_CREDIT,
            courier_id=courier_id,
            details={
                "current_balance": current_balance,
                "required_amount": required_amount,
                "credit_limit": credit_limit,
                "available_credit": current_balance - credit_limit
            }
        )


class WalletNotFoundError(WalletException):
    """Raised when wallet is not found"""

    def __init__(self, courier_id: int):
        super().__init__(
            message=f"Wallet not found for courier {courier_id}",
            error_code=ErrorCode.WALLET_NOT_FOUND,
            courier_id=courier_id
        )
        self.status_code = 404


class ExternalServiceException(AppException):
    """Base exception for external service errors"""

    def __init__(
        self,
        service_name: str,
        message: str,
        error_code: ErrorCode = ErrorCode.EXTERNAL_SERVICE_UNAVAILABLE,
        details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=503,
            details=details
        )
        self.details["service"] = service_name


class TelegramError(ExternalServiceException):
    """Raised when Telegram API fails"""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            service_name="telegram",
            message=f"Telegram API error: {message}",
            error_code=ErrorCode.TELEGRAM_ERROR,
            details=details
        )

    @classmethod
    def from_response(
        cls,
        operation: str,
        response: Any,
        *,
        message: str | None = None,
        max_response_chars: int = 500
    ) -> "TelegramError":
        """
        יצירת TelegramError מתוך HTTP response בצורה עקבית.

        Args:
            operation: שם הפעולה (לדוגמה: sendMessage, sendPhoto)
            response: אובייקט response (למשל httpx.Response)
            message: הודעת שגיאה מותאמת (אם לא סופק - נבנית אוטומטית)
            max_response_chars: אורך מקסימלי לשמירת response_text (מניעת לוגים גדולים)
        """
        status_code = getattr(response, "status_code", None)
        response_text = getattr(response, "text", "") or ""
        return cls(
            message=message or f"{operation} returned status {status_code}",
            details={
                "operation": operation,
                "status_code": status_code,
                "response_text": response_text[:max_response_chars],
            },
        )


class WhatsAppError(ExternalServiceException):
    """Raised when WhatsApp API fails"""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            service_name="whatsapp",
            message=f"WhatsApp API error: {message}",
            error_code=ErrorCode.WHATSAPP_ERROR,
            details=details
        )

    @classmethod
    def from_response(
        cls,
        operation: str,
        response: Any,
        *,
        message: str | None = None,
        max_response_chars: int = 500
    ) -> "WhatsAppError":
        """
        יצירת WhatsAppError מתוך HTTP response בצורה עקבית.

        Args:
            operation: שם הפעולה (לדוגמה: send, send-media)
            response: אובייקט response (למשל httpx.Response)
            message: הודעת שגיאה מותאמת (אם לא סופק - נבנית אוטומטית)
            max_response_chars: אורך מקסימלי לשמירת response_text (מניעת לוגים גדולים)
        """
        status_code = getattr(response, "status_code", None)
        response_text = getattr(response, "text", "") or ""
        return cls(
            message=message or f"{operation} returned status {status_code}",
            details={
                "operation": operation,
                "status_code": status_code,
                "response_text": response_text[:max_response_chars],
            },
        )


class ServiceTimeoutError(ExternalServiceException):
    """Raised when external service times out"""

    def __init__(self, service_name: str, timeout_seconds: float):
        super().__init__(
            service_name=service_name,
            message=f"{service_name} request timed out after {timeout_seconds}s",
            error_code=ErrorCode.EXTERNAL_SERVICE_TIMEOUT,
            details={"timeout_seconds": timeout_seconds}
        )


class CircuitBreakerOpenError(ExternalServiceException):
    """Raised when circuit breaker is open"""

    def __init__(self, service_name: str, retry_after_seconds: float):
        super().__init__(
            service_name=service_name,
            message=f"{service_name} is temporarily unavailable (circuit breaker open)",
            error_code=ErrorCode.EXTERNAL_SERVICE_UNAVAILABLE,
            details={"retry_after_seconds": retry_after_seconds}
        )


class StateMachineException(AppException):
    """Base exception for state machine errors"""

    def __init__(
        self,
        message: str,
        error_code: ErrorCode,
        details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=400,
            details=details
        )


class InvalidStateTransitionError(StateMachineException):
    """Raised when state transition is not allowed"""

    def __init__(self, current_state: str, target_state: str, user_id: int | None = None):
        super().__init__(
            message=f"Invalid transition from '{current_state}' to '{target_state}'",
            error_code=ErrorCode.INVALID_STATE_TRANSITION,
            details={
                "current_state": current_state,
                "target_state": target_state,
                "user_id": user_id
            }
        )
