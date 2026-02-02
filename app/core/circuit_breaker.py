"""
Circuit Breaker Pattern Implementation

Provides protection for external service calls to prevent cascade failures.
"""
import asyncio
import threading
import time
from enum import Enum
from typing import Callable, TypeVar, ParamSpec
from dataclasses import dataclass, field
from functools import wraps

from app.core.logging import get_logger
from app.core.exceptions import CircuitBreakerOpenError

logger = get_logger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Failing, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker"""
    failure_threshold: int = 5         # Failures before opening
    success_threshold: int = 2          # Successes in half-open to close
    timeout_seconds: float = 30.0       # Time before trying half-open
    half_open_max_calls: int = 3        # Max calls in half-open state


@dataclass
class CircuitBreakerState:
    """State tracking for circuit breaker"""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    half_open_calls: int = 0


class CircuitBreaker:
    """
    Circuit breaker for external service protection.

    States:
    - CLOSED: Normal operation, tracking failures
    - OPEN: Service is failing, block all requests
    - HALF_OPEN: Testing if service recovered
    """

    # Class-level storage for circuit breakers (singleton per service)
    _instances: dict[str, "CircuitBreaker"] = {}
    # נעילה ברמת המחלקה להגנה על יצירת singletons
    _instances_lock = threading.Lock()

    def __init__(
        self,
        service_name: str,
        config: CircuitBreakerConfig | None = None
    ):
        self.service_name = service_name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState()
        # שימוש ב-threading.Lock במקום asyncio.Lock כדי לתמוך ב-event loops שונים ב-Celery
        self._lock = threading.Lock()

    @classmethod
    def get_instance(
        cls,
        service_name: str,
        config: CircuitBreakerConfig | None = None
    ) -> "CircuitBreaker":
        """Get or create circuit breaker instance for a service"""
        # בדיקה מהירה ללא נעילה (double-checked locking pattern)
        if service_name not in cls._instances:
            with cls._instances_lock:
                # בדיקה נוספת בתוך הנעילה למניעת race condition
                if service_name not in cls._instances:
                    cls._instances[service_name] = cls(service_name, config)
        return cls._instances[service_name]

    @classmethod
    def reset_all(cls) -> None:
        """Reset all circuit breakers (for testing)"""
        with cls._instances_lock:
            cls._instances.clear()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state"""
        return self._state.state

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)"""
        return self._state.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking requests)"""
        return self._state.state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing)"""
        return self._state.state == CircuitState.HALF_OPEN

    def _should_attempt_reset(self) -> bool:
        """Check if enough time passed to try half-open"""
        if self._state.state != CircuitState.OPEN:
            return False

        time_since_failure = time.time() - self._state.last_failure_time
        return time_since_failure >= self.config.timeout_seconds

    async def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state (async version for backwards compatibility)"""
        self._transition_to_sync(new_state)

    def _transition_to_sync(self, new_state: CircuitState) -> None:
        """Transition to a new state (sync version for use with threading.Lock)"""
        old_state = self._state.state
        self._state.state = new_state

        if new_state == CircuitState.HALF_OPEN:
            self._state.half_open_calls = 0
            self._state.success_count = 0

        if new_state == CircuitState.CLOSED:
            self._state.failure_count = 0
            self._state.success_count = 0

        logger.info(
            f"Circuit breaker '{self.service_name}' transitioned",
            extra_data={
                "service": self.service_name,
                "old_state": old_state.value,
                "new_state": new_state.value
            }
        )

    async def record_success(self) -> None:
        """Record a successful call"""
        with self._lock:
            if self._state.state == CircuitState.HALF_OPEN:
                self._state.success_count += 1
                if self._state.success_count >= self.config.success_threshold:
                    self._transition_to_sync(CircuitState.CLOSED)
            elif self._state.state == CircuitState.CLOSED:
                # Reset failure count on success
                self._state.failure_count = 0

    async def record_failure(self, error: Exception | None = None) -> None:
        """Record a failed call"""
        with self._lock:
            self._state.failure_count += 1
            self._state.last_failure_time = time.time()

            logger.warning(
                f"Circuit breaker '{self.service_name}' recorded failure",
                extra_data={
                    "service": self.service_name,
                    "failure_count": self._state.failure_count,
                    "threshold": self.config.failure_threshold,
                    "error": str(error) if error else None
                }
            )

            if self._state.state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                self._transition_to_sync(CircuitState.OPEN)
            elif self._state.failure_count >= self.config.failure_threshold:
                self._transition_to_sync(CircuitState.OPEN)

    async def can_execute(self) -> bool:
        """Check if a request can be executed"""
        with self._lock:
            if self._state.state == CircuitState.CLOSED:
                return True

            if self._state.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._transition_to_sync(CircuitState.HALF_OPEN)
                    return True
                return False

            if self._state.state == CircuitState.HALF_OPEN:
                if self._state.half_open_calls < self.config.half_open_max_calls:
                    self._state.half_open_calls += 1
                    return True
                return False

            return False

    def get_retry_after(self) -> float:
        """Get seconds until circuit might close"""
        if self._state.state != CircuitState.OPEN:
            return 0.0

        time_since_failure = time.time() - self._state.last_failure_time
        remaining = self.config.timeout_seconds - time_since_failure
        return max(0.0, remaining)

    async def execute(
        self,
        func: Callable[P, T],
        *args: P.args,
        **kwargs: P.kwargs
    ) -> T:
        """
        Execute a function with circuit breaker protection.

        Args:
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Result of the function

        Raises:
            CircuitBreakerOpenError: If circuit is open
        """
        if not await self.can_execute():
            retry_after = self.get_retry_after()
            raise CircuitBreakerOpenError(self.service_name, retry_after)

        try:
            # Handle both sync and async functions
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            await self.record_success()
            return result
        except Exception as e:
            await self.record_failure(e)
            raise


def circuit_breaker(
    service_name: str,
    config: CircuitBreakerConfig | None = None
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator to add circuit breaker protection to a function.

    Usage:
        @circuit_breaker("telegram")
        async def send_telegram_message(chat_id: str, text: str) -> bool:
            ...

    Note: This decorator is designed for async functions. For sync functions,
    use the CircuitBreaker class directly with a new event loop.
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        cb = CircuitBreaker.get_instance(service_name, config)

        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return await cb.execute(func, *args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # Create a new event loop to avoid issues with already-running loops
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(cb.execute(func, *args, **kwargs))
            finally:
                loop.close()

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    return decorator


# Pre-configured circuit breakers for common services
def get_telegram_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for Telegram API"""
    return CircuitBreaker.get_instance(
        "telegram",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0
        )
    )


def get_whatsapp_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for WhatsApp API"""
    return CircuitBreaker.get_instance(
        "whatsapp",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=2,
            timeout_seconds=30.0
        )
    )
