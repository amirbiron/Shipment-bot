"""
Tests for Circuit Breaker Pattern
"""
import pytest
import asyncio
from unittest.mock import AsyncMock

from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    circuit_breaker
)
from app.core.exceptions import CircuitBreakerOpenError


class TestCircuitBreaker:
    """Tests for circuit breaker functionality"""

    @pytest.fixture
    def config(self) -> CircuitBreakerConfig:
        """Create test configuration with fast timeouts"""
        return CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=2,
            timeout_seconds=0.1,  # Fast timeout for tests
            half_open_max_calls=2
        )

    @pytest.fixture
    def breaker(self, config: CircuitBreakerConfig) -> CircuitBreaker:
        """Create circuit breaker for testing"""
        return CircuitBreaker("test-service", config)

    @pytest.mark.unit
    async def test_initial_state_is_closed(self, breaker: CircuitBreaker):
        """Circuit should start in closed state"""
        assert breaker.is_closed
        assert not breaker.is_open
        assert not breaker.is_half_open

    @pytest.mark.unit
    async def test_successful_execution_keeps_closed(self, breaker: CircuitBreaker):
        """Successful executions should keep circuit closed"""
        async def success_func():
            return "success"

        result = await breaker.execute(success_func)

        assert result == "success"
        assert breaker.is_closed

    @pytest.mark.unit
    async def test_failures_open_circuit(self, breaker: CircuitBreaker):
        """Enough failures should open the circuit"""
        async def fail_func():
            raise Exception("Test failure")

        # Trigger failures up to threshold
        for _ in range(3):
            with pytest.raises(Exception):
                await breaker.execute(fail_func)

        assert breaker.is_open

    @pytest.mark.unit
    async def test_open_circuit_blocks_requests(self, breaker: CircuitBreaker):
        """Open circuit should block requests"""
        async def fail_func():
            raise Exception("Test failure")

        # Open the circuit
        for _ in range(3):
            with pytest.raises(Exception):
                await breaker.execute(fail_func)

        # Next request should be blocked
        async def success_func():
            return "success"

        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            await breaker.execute(success_func)

        assert "test-service" in str(exc_info.value)

    @pytest.mark.unit
    async def test_circuit_transitions_to_half_open(self, breaker: CircuitBreaker):
        """Circuit should transition to half-open after timeout"""
        async def fail_func():
            raise Exception("Test failure")

        # Open the circuit
        for _ in range(3):
            with pytest.raises(Exception):
                await breaker.execute(fail_func)

        assert breaker.is_open

        # Wait for timeout
        await asyncio.sleep(0.15)

        # Check if can execute (should transition to half-open)
        can_execute = await breaker.can_execute()
        assert can_execute
        assert breaker.is_half_open

    @pytest.mark.unit
    async def test_half_open_success_closes_circuit(self, breaker: CircuitBreaker):
        """Successful calls in half-open should close circuit"""
        async def fail_func():
            raise Exception("Test failure")

        async def success_func():
            return "success"

        # Open the circuit
        for _ in range(3):
            with pytest.raises(Exception):
                await breaker.execute(fail_func)

        # Wait for timeout
        await asyncio.sleep(0.15)

        # Successful calls should close circuit
        for _ in range(2):
            result = await breaker.execute(success_func)
            assert result == "success"

        assert breaker.is_closed

    @pytest.mark.unit
    async def test_half_open_failure_reopens_circuit(self, breaker: CircuitBreaker):
        """Failure in half-open should reopen circuit"""
        async def fail_func():
            raise Exception("Test failure")

        # Open the circuit
        for _ in range(3):
            with pytest.raises(Exception):
                await breaker.execute(fail_func)

        # Wait for timeout
        await asyncio.sleep(0.15)

        # Failure should reopen circuit
        with pytest.raises(Exception):
            await breaker.execute(fail_func)

        assert breaker.is_open

    @pytest.mark.unit
    async def test_get_retry_after(self, breaker: CircuitBreaker):
        """Should return correct retry-after time"""
        async def fail_func():
            raise Exception("Test failure")

        # Open the circuit
        for _ in range(3):
            with pytest.raises(Exception):
                await breaker.execute(fail_func)

        retry_after = breaker.get_retry_after()
        assert retry_after > 0
        assert retry_after <= 0.1  # Should be less than timeout

    @pytest.mark.unit
    async def test_singleton_pattern(self):
        """Should return same instance for same service"""
        config = CircuitBreakerConfig()
        cb1 = CircuitBreaker.get_instance("singleton-test", config)
        cb2 = CircuitBreaker.get_instance("singleton-test")

        assert cb1 is cb2

    @pytest.mark.unit
    async def test_decorator(self):
        """Test circuit breaker decorator"""
        call_count = 0

        @circuit_breaker("decorator-test", CircuitBreakerConfig(failure_threshold=2))
        async def decorated_func(should_fail: bool = False):
            nonlocal call_count
            call_count += 1
            if should_fail:
                raise Exception("Decorated failure")
            return "decorated success"

        # Successful call
        result = await decorated_func()
        assert result == "decorated success"

        # Failures to open circuit
        for _ in range(2):
            with pytest.raises(Exception):
                await decorated_func(should_fail=True)

        # Should be blocked now
        with pytest.raises(CircuitBreakerOpenError):
            await decorated_func()
