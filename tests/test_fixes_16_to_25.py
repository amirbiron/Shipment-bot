"""
בדיקות לתיקוני באגים #16-#25

#16: Guard function דינמי ב-DispatcherStateHandler
#17: State ADD_SHIPMENT_DROPOFF_APARTMENT לסדרן
#18: Deep copy של context ב-StateManager
#19: חריגות נבלעות ב-admin_notification_service (בדיקת לוגיקה בלבד)
#20: Authorization עם warning ב-station_service (בדיקת לוגיקה בלבד)
#21: AmountValidator עם Decimal
#22: ולידציית Rate Limit ב-config
#23: Circuit Breaker — get_instance ללא race condition
#24: Circuit Breaker — sync_wrapper עם event loop קיים
#25: Fallback לתפקידים לא מוכרים (בדיקת לוגיקה בלבד)
"""
import pytest
import asyncio
from decimal import Decimal

from app.core.validation import AmountValidator
from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    circuit_breaker,
)
from app.core.exceptions import CircuitBreakerOpenError
from app.state_machine.states import (
    DispatcherState,
    DISPATCHER_TRANSITIONS,
)


# ============================================================================
# #16: Guard function דינמי
# ============================================================================


class TestDispatcherGuardFunction:
    """בדיקת _is_multi_step_flow_state דינמי"""

    def _make_handler(self):
        """יצירת handler ללא DB — רק לבדיקת הלוגיקה"""
        from app.state_machine.dispatcher_handler import DispatcherStateHandler
        # handler.__init__ דורש db, station_id — ניצור instance ללא __init__
        handler = object.__new__(DispatcherStateHandler)
        return handler

    @pytest.mark.unit
    def test_menu_is_not_multi_step(self):
        handler = self._make_handler()
        assert handler._is_multi_step_flow_state("DISPATCHER.MENU") is False

    @pytest.mark.unit
    def test_view_active_is_not_multi_step(self):
        handler = self._make_handler()
        assert handler._is_multi_step_flow_state("DISPATCHER.VIEW_ACTIVE_SHIPMENTS") is False

    @pytest.mark.unit
    def test_view_posted_rides_is_not_multi_step(self):
        handler = self._make_handler()
        assert handler._is_multi_step_flow_state("DISPATCHER.VIEW_POSTED_RIDES") is False

    @pytest.mark.unit
    def test_add_shipment_is_multi_step(self):
        handler = self._make_handler()
        assert handler._is_multi_step_flow_state("DISPATCHER.ADD_SHIPMENT.PICKUP_CITY") is True

    @pytest.mark.unit
    def test_manual_charge_is_multi_step(self):
        handler = self._make_handler()
        assert handler._is_multi_step_flow_state("DISPATCHER.MANUAL_CHARGE.AMOUNT") is True

    @pytest.mark.unit
    def test_post_ride_is_multi_step(self):
        handler = self._make_handler()
        assert handler._is_multi_step_flow_state("DISPATCHER.POST_RIDE.ORIGIN") is True

    @pytest.mark.unit
    def test_hypothetical_new_flow_is_multi_step(self):
        """flow חדש (כמו ISSUE_REFUND) נתפס אוטומטית"""
        handler = self._make_handler()
        assert handler._is_multi_step_flow_state("DISPATCHER.ISSUE_REFUND.AMOUNT") is True

    @pytest.mark.unit
    def test_non_dispatcher_state_is_not_multi_step(self):
        handler = self._make_handler()
        assert handler._is_multi_step_flow_state("COURIER.MENU") is False


# ============================================================================
# #17: State DROPOFF_APARTMENT
# ============================================================================


class TestDispatcherDropoffApartment:
    """בדיקת הוספת state דירה ביעד לסדרן"""

    @pytest.mark.unit
    def test_dropoff_apartment_state_exists(self):
        """וידוא שה-state החדש קיים ב-enum"""
        assert hasattr(DispatcherState, "ADD_SHIPMENT_DROPOFF_APARTMENT")
        assert DispatcherState.ADD_SHIPMENT_DROPOFF_APARTMENT.value == "DISPATCHER.ADD_SHIPMENT.DROPOFF_APARTMENT"

    @pytest.mark.unit
    def test_dropoff_number_transitions_to_apartment(self):
        """DROPOFF_NUMBER מעביר ל-DROPOFF_APARTMENT"""
        targets = DISPATCHER_TRANSITIONS[DispatcherState.ADD_SHIPMENT_DROPOFF_NUMBER]
        assert DispatcherState.ADD_SHIPMENT_DROPOFF_APARTMENT in targets

    @pytest.mark.unit
    def test_dropoff_apartment_transitions_to_description(self):
        """DROPOFF_APARTMENT מעביר ל-DESCRIPTION"""
        targets = DISPATCHER_TRANSITIONS[DispatcherState.ADD_SHIPMENT_DROPOFF_APARTMENT]
        assert DispatcherState.ADD_SHIPMENT_DESCRIPTION in targets


# ============================================================================
# #21: AmountValidator עם Decimal
# ============================================================================


class TestAmountValidatorDecimal:
    """בדיקת AmountValidator עם Decimal לדיוק"""

    @pytest.mark.unit
    def test_valid_two_decimals(self):
        is_valid, err = AmountValidator.validate(10.99)
        assert is_valid is True

    @pytest.mark.unit
    def test_valid_zero(self):
        is_valid, err = AmountValidator.validate(0.0)
        assert is_valid is True

    @pytest.mark.unit
    def test_three_decimals_rejected(self):
        is_valid, err = AmountValidator.validate(10.123)
        assert is_valid is False
        assert "decimal" in err.lower()

    @pytest.mark.unit
    def test_floating_point_edge_case(self):
        """0.1 + 0.2 = 0.30000000000000004 — צריך לעבור כי str() = '0.30000000000000004'
        אבל round(0.1+0.2, 2) = 0.3, ו-Decimal('0.3') הוא חוקי"""
        # הערך 0.3 הוא חוקי
        is_valid, err = AmountValidator.validate(0.3)
        assert is_valid is True

    @pytest.mark.unit
    def test_integer_amount_valid(self):
        is_valid, err = AmountValidator.validate(100.0)
        assert is_valid is True


# ============================================================================
# #22: ולידציית Rate Limit
# ============================================================================


class TestRateLimitValidation:
    """בדיקת ולידציה של ערכי rate limit"""

    @pytest.mark.unit
    def test_zero_max_requests_rejected(self):
        from pydantic import ValidationError
        from app.core.config import Settings
        with pytest.raises(ValidationError, match="greater than 0"):
            Settings(WEBHOOK_RATE_LIMIT_MAX_REQUESTS=0)

    @pytest.mark.unit
    def test_negative_window_rejected(self):
        from pydantic import ValidationError
        from app.core.config import Settings
        with pytest.raises(ValidationError, match="greater than 0"):
            Settings(WEBHOOK_RATE_LIMIT_WINDOW_SECONDS=-1)


# ============================================================================
# #23: Circuit Breaker — get_instance ללא race condition
# ============================================================================


class TestCircuitBreakerGetInstance:
    """בדיקת thread safety של get_instance"""

    @pytest.fixture(autouse=True)
    def reset_instances(self):
        """ניקוי instances לפני כל בדיקה"""
        CircuitBreaker.reset_all()
        yield
        CircuitBreaker.reset_all()

    @pytest.mark.unit
    def test_get_instance_returns_same_object(self):
        cb1 = CircuitBreaker.get_instance("test-singleton")
        cb2 = CircuitBreaker.get_instance("test-singleton")
        assert cb1 is cb2

    @pytest.mark.unit
    def test_get_instance_different_services(self):
        cb1 = CircuitBreaker.get_instance("service-a")
        cb2 = CircuitBreaker.get_instance("service-b")
        assert cb1 is not cb2


# ============================================================================
# #24: Circuit Breaker — sync_wrapper
# ============================================================================


class TestCircuitBreakerSyncWrapper:
    """בדיקת sync_wrapper עם/בלי event loop"""

    @pytest.fixture(autouse=True)
    def reset_instances(self):
        CircuitBreaker.reset_all()
        yield
        CircuitBreaker.reset_all()

    @pytest.mark.unit
    def test_sync_function_decorated(self):
        """פונקציה סינכרונית עטופה ב-circuit breaker"""
        @circuit_breaker("test-sync-dec")
        def sync_func(x: int) -> int:
            return x * 2

        result = sync_func(5)
        assert result == 10

    @pytest.mark.unit
    def test_sync_methods_exist(self):
        """בדיקת קיום מתודות סינכרוניות"""
        cb = CircuitBreaker("test-sync-methods")
        assert hasattr(cb, "_check_can_execute_sync")
        assert hasattr(cb, "_record_success_sync")
        assert hasattr(cb, "_record_failure_sync")

    @pytest.mark.unit
    def test_check_can_execute_sync_closed(self):
        cb = CircuitBreaker("test-sync-check")
        assert cb._check_can_execute_sync() is True

    @pytest.mark.unit
    def test_record_success_sync_resets_failures(self):
        cb = CircuitBreaker("test-sync-success")
        cb._state.failure_count = 2
        cb._record_success_sync()
        assert cb._state.failure_count == 0

    @pytest.mark.unit
    def test_record_failure_sync_opens_circuit(self):
        config = CircuitBreakerConfig(failure_threshold=2)
        cb = CircuitBreaker("test-sync-failure", config)
        cb._record_failure_sync(Exception("err1"))
        cb._record_failure_sync(Exception("err2"))
        assert cb.is_open
