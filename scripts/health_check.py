#!/usr/bin/env python3
"""
סקריפט בדיקות בריאות למערכת - להרצה ב-Render Shell

הרצה (מתוך תיקיית הפרויקט):
    python scripts/health_check.py

או עם בדיקות ספציפיות:
    python scripts/health_check.py --only validation,circuit_breaker

בדיקות זמינות:
    config, validation, circuit_breaker, logging, exceptions, database
"""
import sys
import os
import asyncio
import argparse
from datetime import datetime
from pathlib import Path

# הוספת תיקיית הפרויקט ל-path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class Colors:
    """צבעים לפלט בטרמינל"""
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def print_header(title: str) -> None:
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}")
    print(f" {title}")
    print(f"{'='*60}{Colors.RESET}\n")


def print_result(test_name: str, passed: bool, details: str = "") -> None:
    status = f"{Colors.GREEN}✓ PASS{Colors.RESET}" if passed else f"{Colors.RED}✗ FAIL{Colors.RESET}"
    print(f"  {status} {test_name}")
    if details:
        print(f"         {Colors.YELLOW}{details}{Colors.RESET}")


def print_section(title: str) -> None:
    print(f"\n{Colors.BOLD}▶ {title}{Colors.RESET}")


class HealthChecker:
    """בודק בריאות המערכת"""

    def __init__(self):
        self.results: list[tuple[str, bool, str]] = []
        self.total_passed = 0
        self.total_failed = 0

    def record(self, test_name: str, passed: bool, details: str = "") -> None:
        self.results.append((test_name, passed, details))
        if passed:
            self.total_passed += 1
        else:
            self.total_failed += 1
        print_result(test_name, passed, details)

    # =========================================================================
    # בדיקות Validation
    # =========================================================================
    def test_validation(self) -> None:
        print_section("Input Validation")

        try:
            from app.core.validation import (
                PhoneNumberValidator,
                AddressValidator,
                NameValidator,
                TextSanitizer
            )

            # בדיקת טלפון תקין
            valid_phone = PhoneNumberValidator.validate("0501234567")
            self.record("Phone validation (valid)", valid_phone)

            # בדיקת טלפון לא תקין
            invalid_phone = not PhoneNumberValidator.validate("123")
            self.record("Phone validation (invalid rejected)", invalid_phone)

            # בדיקת נרמול טלפון
            normalized = PhoneNumberValidator.normalize("050-123-4567")
            self.record(
                "Phone normalization",
                normalized == "+972501234567",
                f"Result: {normalized}"
            )

            # בדיקת מיסוך טלפון (פרטיות)
            masked = PhoneNumberValidator.mask("+972501234567")
            is_masked = "****" in masked and "4567" not in masked
            self.record(
                "Phone masking (privacy)",
                is_masked,
                f"Result: {masked}"
            )

            # בדיקת כתובת תקינה
            valid_address = AddressValidator.validate("רחוב הרצל 1, תל אביב")
            self.record("Address validation (valid)", valid_address)

            # בדיקת כתובת עם Union (לא SQL injection)
            union_address = AddressValidator.validate("123 Union Street, New York")
            self.record(
                "Address with 'Union' (not injection)",
                union_address,
                "Should allow legitimate addresses"
            )

            # בדיקת SQL injection
            is_safe, pattern = TextSanitizer.check_for_injection("'; DROP TABLE users; --")
            self.record(
                "SQL injection detection",
                not is_safe,
                f"Detected pattern: {pattern}" if pattern else ""
            )

            # בדיקת סניטציה (ללא HTML escaping בשמירה)
            original = "O'Brien"
            sanitized = TextSanitizer.sanitize(original)
            self.record(
                "Sanitization preserves apostrophes",
                sanitized == original,
                f"Input: {original}, Output: {sanitized}"
            )

        except Exception as e:
            self.record("Validation module import", False, str(e))

    # =========================================================================
    # בדיקות Circuit Breaker
    # =========================================================================
    def test_circuit_breaker(self) -> None:
        print_section("Circuit Breaker")

        try:
            from app.core.circuit_breaker import (
                CircuitBreaker,
                CircuitBreakerConfig,
                CircuitState
            )

            # ניקוי לפני בדיקות
            CircuitBreaker.reset_all()

            # בדיקת singleton
            cb1 = CircuitBreaker.get_instance("test-health")
            cb2 = CircuitBreaker.get_instance("test-health")
            self.record("Singleton pattern", cb1 is cb2)

            # בדיקת מצב התחלתי
            self.record("Initial state is CLOSED", cb1.is_closed)

            # בדיקת thread safety
            import threading
            results = []

            def get_instance():
                cb = CircuitBreaker.get_instance("thread-test")
                results.append(id(cb))

            threads = [threading.Thread(target=get_instance) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            unique_ids = len(set(results))
            self.record(
                "Thread-safe singleton creation",
                unique_ids == 1,
                f"5 threads, {unique_ids} unique instance(s)"
            )

            # בדיקת multi event loop (Celery compatibility)
            cb = CircuitBreaker.get_instance("celery-test")

            loop1 = asyncio.new_event_loop()
            asyncio.set_event_loop(loop1)
            result1 = loop1.run_until_complete(cb.can_execute())
            loop1.close()

            loop2 = asyncio.new_event_loop()
            asyncio.set_event_loop(loop2)
            result2 = loop2.run_until_complete(cb.can_execute())
            loop2.close()

            self.record(
                "Multi event-loop compatibility (Celery)",
                result1 and result2,
                "Works across different event loops"
            )

            # ניקוי
            CircuitBreaker.reset_all()

        except Exception as e:
            self.record("Circuit breaker tests", False, str(e))

    # =========================================================================
    # בדיקות Logging
    # =========================================================================
    def test_logging(self) -> None:
        print_section("Logging Infrastructure")

        try:
            from app.core.logging import (
                get_logger,
                set_correlation_id,
                get_correlation_id
            )

            # בדיקת יצירת logger
            logger = get_logger("health_check")
            self.record("Logger creation", logger is not None)

            # בדיקת correlation ID
            cid = set_correlation_id()
            retrieved = get_correlation_id()
            self.record(
                "Correlation ID generation",
                cid == retrieved and len(cid) > 0,
                f"ID: {cid[:8]}..."
            )

            # בדיקת custom correlation ID
            custom_id = "test-12345"
            set_correlation_id(custom_id)
            self.record(
                "Custom correlation ID",
                get_correlation_id() == custom_id
            )

        except Exception as e:
            self.record("Logging tests", False, str(e))

    # =========================================================================
    # בדיקות Exceptions
    # =========================================================================
    def test_exceptions(self) -> None:
        print_section("Custom Exceptions")

        try:
            from app.core.exceptions import (
                DeliveryNotFoundError,
                InsufficientCreditError,
                CircuitBreakerOpenError,
                ErrorCode
            )

            # בדיקת exception עם error code
            exc = DeliveryNotFoundError(delivery_id=123)
            self.record(
                "DeliveryNotFoundError",
                exc.error_code == ErrorCode.DELIVERY_NOT_FOUND.value,
                f"Error code: {exc.error_code}"
            )

            # בדיקת InsufficientCreditError
            exc = InsufficientCreditError(
                courier_id=1,
                current_balance=50.0,
                required_amount=100.0,
                credit_limit=-500.0
            )
            has_details = (
                exc.details.get("current_balance") == 50.0 and
                exc.details.get("required_amount") == 100.0
            )
            self.record("InsufficientCreditError with details", has_details)

            # בדיקת CircuitBreakerOpenError
            exc = CircuitBreakerOpenError("telegram", retry_after_seconds=30.0)
            self.record(
                "CircuitBreakerOpenError",
                "telegram" in exc.message and exc.details.get("retry_after_seconds") == 30.0
            )

        except Exception as e:
            self.record("Exception tests", False, str(e))

    # =========================================================================
    # בדיקות Database
    # =========================================================================
    def test_database(self) -> None:
        print_section("Database Connectivity")

        try:
            from app.db.database import engine
            from sqlalchemy import text

            async def check_db():
                async with engine.connect() as conn:
                    result = await conn.execute(text("SELECT 1"))
                    return result.scalar() == 1

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                connected = loop.run_until_complete(check_db())
                self.record("Database connection", connected)
            finally:
                loop.close()

        except Exception as e:
            self.record("Database connection", False, str(e))

    # =========================================================================
    # בדיקות Configuration
    # =========================================================================
    def test_config(self) -> None:
        print_section("Configuration")

        try:
            from app.core.config import settings

            self.record(
                "Settings loaded",
                settings is not None,
                f"App: {settings.APP_NAME}"
            )

            # בדיקת משתנים חיוניים
            has_db = bool(settings.DATABASE_URL)
            self.record(
                "DATABASE_URL configured",
                has_db,
                "***" if has_db else "MISSING!"
            )

        except Exception as e:
            self.record("Configuration tests", False, str(e))

    # =========================================================================
    # הרצת כל הבדיקות
    # =========================================================================
    def run_all(self, only: list[str] | None = None) -> bool:
        print_header(f"בדיקות בריאות המערכת - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        tests = {
            "config": self.test_config,
            "validation": self.test_validation,
            "circuit_breaker": self.test_circuit_breaker,
            "logging": self.test_logging,
            "exceptions": self.test_exceptions,
            "database": self.test_database,
        }

        if only:
            tests = {k: v for k, v in tests.items() if k in only}

        for test_func in tests.values():
            try:
                test_func()
            except Exception as e:
                print(f"{Colors.RED}Error running test: {e}{Colors.RESET}")

        # סיכום
        print_header("סיכום")
        total = self.total_passed + self.total_failed

        if self.total_failed == 0:
            print(f"{Colors.GREEN}{Colors.BOLD}✓ כל הבדיקות עברו בהצלחה! ({total}/{total}){Colors.RESET}")
        else:
            print(f"{Colors.RED}{Colors.BOLD}✗ נכשלו {self.total_failed} בדיקות מתוך {total}{Colors.RESET}")
            print(f"\n{Colors.YELLOW}בדיקות שנכשלו:{Colors.RESET}")
            for name, passed, details in self.results:
                if not passed:
                    print(f"  - {name}: {details}")

        print()
        return self.total_failed == 0


def main():
    parser = argparse.ArgumentParser(description="בדיקות בריאות המערכת")
    parser.add_argument(
        "--only",
        type=str,
        help="הרץ רק בדיקות ספציפיות (מופרדות בפסיק): config,validation,circuit_breaker,logging,exceptions,database"
    )
    args = parser.parse_args()

    only = args.only.split(",") if args.only else None

    checker = HealthChecker()
    success = checker.run_all(only)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
