# Shipment Bot - הנחיות לריפו

## סקירת הפרויקט
מערכת בוט לשליחויות לפלטפורמות WhatsApp ו-Telegram, בנויה עם FastAPI, PostgreSQL, Celery ו-Redis.

## ארכיטקטורה
```
Bot Gateway (Webhooks) → Application Layer (State Machine) →
Domain Layer (Services) → Data Layer (PostgreSQL) ↔
Task Queue (Celery + Redis)
```

---

## כללים כלליים

- סכם תמיד את ה-PR גם בצ'אט וגם בתיאור ה-PR בגיטהאב - **בעברית**
- הערות בקוד - **בעברית**

---

## סטנדרטים לקוד

### לוגים
**אסור להשתמש ב-`print()`** - תמיד להשתמש בלוגים מובנים:

```python
from app.core.logging import get_logger

logger = get_logger(__name__)

# נכון
logger.info("Operation completed", extra_data={"user_id": 123, "action": "capture"})
logger.error("Failed to send message", extra_data={"error": str(e)}, exc_info=True)

# לא נכון
print(f"Operation completed for user {user_id}")
```

### פרטיות מספרי טלפון
**חובה למסך מספרי טלפון בלוגים** באמצעות `PhoneNumberValidator.mask()`:

```python
from app.core.validation import PhoneNumberValidator

# נכון - מסתיר את 4 הספרות האחרונות
logger.info("Message sent", extra_data={"phone": PhoneNumberValidator.mask(phone)})
# פלט: +97250123****

# לא נכון - חושף את המספר
logger.info("Message sent", extra_data={"phone": phone})
```

### ולידציית קלט
**כל קלט מהמשתמש חייב עבור ולידציה** באמצעות validators מ-`app/core/validation.py`:

```python
from app.core.validation import (
    PhoneNumberValidator,
    AddressValidator,
    NameValidator,
    TextSanitizer
)

# ולידציית טלפון
if not PhoneNumberValidator.validate(phone):
    raise ValueError("Invalid phone number")
normalized = PhoneNumberValidator.normalize(phone)

# סניטציה של טקסט (מונע XSS/SQL injection)
safe_text = TextSanitizer.sanitize(user_input)
is_safe, pattern = TextSanitizer.check_for_injection(user_input)
```

### מודלים של Pydantic
**חובה להוסיף field validators לכל מודל Pydantic:**

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

### טיפול בשגיאות
**חובה להשתמש ב-exceptions מותאמים מ-`app/core/exceptions.py`:**

```python
from app.core.exceptions import (
    ValidationException,
    NotFoundException,
    DeliveryNotFoundError,
    InsufficientCreditError
)

# נכון - שגיאה מובנית עם קוד
raise DeliveryNotFoundError(delivery_id=123)

# לא נכון - exception גנרי
raise Exception("Delivery not found")
```

### שירותים חיצוניים
**חובה להשתמש ב-Circuit Breaker לכל קריאת API חיצונית:**

```python
from app.core.circuit_breaker import get_telegram_circuit_breaker

circuit_breaker = get_telegram_circuit_breaker()

async def send_message():
    async def _send():
        # קריאת API כאן
        pass

    return await circuit_breaker.execute(_send)
```

### Type Hints
**כל פונקציה חייבת לכלול type hints:**

```python
# נכון
async def create_delivery(
    sender_id: int,
    pickup_address: str,
    fee: float = 10.0
) -> Delivery:
    ...

# לא נכון
async def create_delivery(sender_id, pickup_address, fee=10.0):
    ...
```

### תיעוד API
**כל endpoint חייב לכלול תיעוד OpenAPI:**

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

## דרישות בדיקות

### הרצת בדיקות
```bash
pip install -r requirements-dev.txt
pytest
pytest --cov=app  # עם כיסוי קוד
```

### מבנה בדיקות
- בדיקות יחידה: `tests/test_*.py`
- להשתמש ב-fixtures מ-`tests/conftest.py`
- לעשות mock לשירותים חיצוניים (Telegram, WhatsApp)

### כתיבת בדיקות
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

## מבנה קבצים

```
app/
├── api/
│   ├── routes/          # נקודות קצה API
│   └── webhooks/        # Telegram/WhatsApp webhooks
├── core/
│   ├── config.py        # הגדרות
│   ├── logging.py       # לוגים מובנים
│   ├── validation.py    # ולידטורים
│   ├── exceptions.py    # exceptions מותאמים
│   ├── circuit_breaker.py
│   └── middleware.py    # middleware לבקשות
├── db/
│   ├── models/          # מודלים של SQLAlchemy
│   └── database.py      # חיבור לDB
├── domain/
│   └── services/        # לוגיקה עסקית
├── state_machine/       # זרימת שיחה
└── workers/
    └── tasks.py         # משימות Celery
```

---

## דפוסי עיצוב מרכזיים

### Transactional Outbox
הודעות נשמרות בטבלת outbox באותה טרנזקציה עם הפעולה העסקית, ומעובדות באופן אסינכרוני על ידי Celery workers.

### State Machine
זרימות שיחה מנוהלות דרך enums של `SenderState` ו-`CourierState` עם מעברים מוגדרים.

### Correlation IDs
כל בקשה מקבלת correlation ID למעקב:
```python
from app.core.logging import set_correlation_id, get_correlation_id

correlation_id = set_correlation_id()  # מייצר אוטומטית אם לא סופק
```

---

## כללי ניתוב Webhook (telegram.py)

### ניתוב לפי תפקיד
**כל `if role ==` חייב לטפל בכל התפקידים - אסור `else` גנרי:**

```python
# לא נכון - else תופס תפקידים לא צפויים
if user.role == UserRole.COURIER:
    ...
else:
    ...  # STATION_OWNER? ADMIN? מי יודע

# נכון - מפורש לכל תפקיד, עם אזהרה ל-fallback
if user.role == UserRole.COURIER:
    ...
elif user.role == UserRole.STATION_OWNER:
    ...
elif user.role == UserRole.SENDER:
    ...
else:
    logger.warning("Unknown role", extra_data={"role": str(user.role)})
```

להשתמש ב-`_route_to_role_menu()` לכל ניתוב איפוס (שורש, #, /start).
**כשמוסיפים תפקיד חדש - חובה לעדכן את `_route_to_role_menu()`.**

### הגנה על זרימות רב-שלביות
**אסור לבדוק `"keyword" in text` ללא guard על state:**

```python
# לא נכון - תופס כתובות כמו "תחנה מרכזית"
if "תחנה" in text:
    return marketing_response()

# נכון - בודקים קודם אם המשתמש באמצע זרימה
if not _is_in_multi_step_flow:
    if "תחנה" in text:
        return marketing_response()
```

ה-guard `_is_in_multi_step_flow` בודק prefixes: `"DISPATCHER."`, `"STATION."`, ו-states של רישום שליח.
**כשמוסיפים prefix חדש ל-state machine - חובה לעדכן את ה-guard.**

### אטומיות בפעולות DB
- כל **read-modify-write על ארנק** חייב `with_for_update()` (נעילת שורה)
- כל שדה שנכתב **חייב להיות באותה טרנזקציה** - לא לעשות commit ואז לעדכן שדה נוסף

---

## צ'קליסט לפיצ'רים דו-פלטפורמיים (Telegram + WhatsApp)

1. **עקביות בין פלטפורמות** - כל לוגיקה חדשה חייבת לעבוד
   זהה בשתי הפלטפורמות. לא לשכפל קוד - להוציא לשירות משותף.
2. **fallback לקבוצה** - כפתורים לא עובדים בקבוצות.
   בכל fallback לקבוצה: keyboard=None + הנחיות טקסטואליות.
3. **auth בטלגרם** - תמיד לזהות לפי from_user.id (מי לחץ),
   לעולם לא לפי chat.id (איפה ההודעה).
4. **background tasks** - להשתמש ב-background_tasks.add_task()
   לשליחת הודעות. לעולם לא asyncio.create_task (בולע exceptions).
5. **סינון מספרי טלפון** - לסנן גם tg: (placeholder) וגם
   @g.us (מזהה קבוצה) לפני שליחת הודעה אישית.
6. **fallback שמות** - תמיד user.full_name or user.name or 'לא צוין'

---

## אסור!

1. **אסור להשתמש ב-`print()`** - להשתמש ב-`logger`
2. **אסור לחשוף מספרי טלפון בלוגים** - להשתמש ב-`PhoneNumberValidator.mask()`
3. **אסור לקבל קלט ללא ולידציה** - להשתמש ב-validators
4. **אסור לקרוא ל-API חיצוני בלי Circuit Breaker**
5. **אסור לכתוב פונקציות בלי type hints**
6. **אסור ליצור endpoints בלי תיעוד OpenAPI**
7. **אסור לעשות commit בלי בדיקות לפיצ'רים חדשים**
8. **אסור `else` גנרי בניתוב לפי תפקיד** - לטפל בכל `UserRole` במפורש
9. **אסור `"keyword" in text` ללא guard** - לבדוק `_is_in_multi_step_flow` קודם
10. **אסור read-modify-write על ארנק בלי `with_for_update()`**
