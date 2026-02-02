# Shipment Bot - Repository Guidelines

## Project Overview
Delivery dispatch bot system for WhatsApp and Telegram platforms, built with FastAPI, PostgreSQL, Celery, and Redis.

## Architecture
```
Bot Gateway (Webhooks) → Application Layer (State Machine) →
Domain Layer (Services) → Data Layer (PostgreSQL) ↔
Task Queue (Celery + Redis)
```

---

## Coding Standards

### Logging
**Never use `print()` statements.** Always use structured logging:

```python
from app.core.logging import get_logger

logger = get_logger(__name__)

# Good
logger.info("Operation completed", extra_data={"user_id": 123, "action": "capture"})
logger.error("Failed to send message", extra_data={"error": str(e)}, exc_info=True)

# Bad
print(f"Operation completed for user {user_id}")
```

### Phone Number Privacy
**Always mask phone numbers in logs** using `PhoneNumberValidator.mask()`:

```python
from app.core.validation import PhoneNumberValidator

# Good - hides last 4 digits
logger.info("Message sent", extra_data={"phone": PhoneNumberValidator.mask(phone)})
# Output: +97250123****

# Bad - exposes the number
logger.info("Message sent", extra_data={"phone": phone})
```

### Input Validation
**All user inputs must be validated** using validators from `app/core/validation.py`:

```python
from app.core.validation import (
    PhoneNumberValidator,
    AddressValidator,
    NameValidator,
    TextSanitizer
)

# Phone validation
if not PhoneNumberValidator.validate(phone):
    raise ValueError("Invalid phone number")
normalized = PhoneNumberValidator.normalize(phone)

# Text sanitization (prevents XSS/SQL injection)
safe_text = TextSanitizer.sanitize(user_input)
is_safe, pattern = TextSanitizer.check_for_injection(user_input)
```

### Pydantic Models
**Add field validators to all Pydantic models:**

```python
from pydantic import BaseModel, field_validator

class UserCreate(BaseModel):
    phone_number: str
    name: str | None = None

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PhoneNumberValidator.validate(v):
            raise ValueError("Invalid phone number format")
        return PhoneNumberValidator.normalize(v)
```

### Error Handling
**Use custom exceptions from `app/core/exceptions.py`:**

```python
from app.core.exceptions import (
    ValidationException,
    NotFoundException,
    DeliveryNotFoundError,
    InsufficientCreditError
)

# Good - structured error with code
raise DeliveryNotFoundError(delivery_id=123)

# Bad - generic exception
raise Exception("Delivery not found")
```

### External Services
**Always use Circuit Breaker for external API calls:**

```python
from app.core.circuit_breaker import get_telegram_circuit_breaker

circuit_breaker = get_telegram_circuit_breaker()

async def send_message():
    async def _send():
        # API call here
        pass

    return await circuit_breaker.execute(_send)
```

### Type Hints
**All functions must have type hints:**

```python
# Good
async def create_delivery(
    sender_id: int,
    pickup_address: str,
    fee: float = 10.0
) -> Delivery:
    ...

# Bad
async def create_delivery(sender_id, pickup_address, fee=10.0):
    ...
```

### API Documentation
**All endpoints must have OpenAPI documentation:**

```python
@router.post(
    "/",
    response_model=DeliveryResponse,
    summary="Create a new delivery",
    description="Creates a new delivery request with pickup and dropoff addresses.",
    responses={
        200: {"description": "Delivery created successfully"},
        422: {"description": "Validation error"}
    },
    tags=["Deliveries"]
)
async def create_delivery(...) -> DeliveryResponse:
    """
    Create a new delivery request.

    - **sender_id**: ID of the sender user
    - **pickup_address**: Full address for pickup
    """
```

---

## Testing Requirements

### Running Tests
```bash
pip install -r requirements-dev.txt
pytest
pytest --cov=app  # with coverage
```

### Test Structure
- Unit tests: `tests/test_*.py`
- Use fixtures from `tests/conftest.py`
- Mock external services (Telegram, WhatsApp)

### Writing Tests
```python
import pytest
from app.core.validation import PhoneNumberValidator

class TestPhoneValidation:
    @pytest.mark.unit
    def test_valid_israeli_phone(self):
        assert PhoneNumberValidator.validate("0501234567") is True

    @pytest.mark.unit
    def test_normalize_phone(self):
        assert PhoneNumberValidator.normalize("050-123-4567") == "+972501234567"
```

---

## File Structure

```
app/
├── api/
│   ├── routes/          # API endpoints
│   └── webhooks/        # Telegram/WhatsApp webhooks
├── core/
│   ├── config.py        # Settings
│   ├── logging.py       # Structured logging
│   ├── validation.py    # Input validators
│   ├── exceptions.py    # Custom exceptions
│   ├── circuit_breaker.py
│   └── middleware.py    # Request middleware
├── db/
│   ├── models/          # SQLAlchemy models
│   └── database.py      # DB connection
├── domain/
│   └── services/        # Business logic
├── state_machine/       # Conversation flow
└── workers/
    └── tasks.py         # Celery tasks
```

---

## Key Patterns

### Transactional Outbox
Messages are saved to outbox table in same transaction as business operation, then processed asynchronously by Celery workers.

### State Machine
Conversation flows managed via `SenderState` and `CourierState` enums with defined transitions.

### Correlation IDs
Every request gets a correlation ID for tracing:
```python
from app.core.logging import set_correlation_id, get_correlation_id

correlation_id = set_correlation_id()  # Auto-generates if not provided
```

---

## Don'ts

1. **Don't use `print()`** - Use `logger`
2. **Don't expose phone numbers in logs** - Use `PhoneNumberValidator.mask()`
3. **Don't accept unvalidated input** - Use validators
4. **Don't call external APIs without Circuit Breaker**
5. **Don't write functions without type hints**
6. **Don't create endpoints without OpenAPI docs**
7. **Don't commit without tests for new functionality**
