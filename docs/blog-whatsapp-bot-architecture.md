# איך לבנות בוט WhatsApp לשליחויות — ארכיטקטורה שעובדת בפרודקשן

> פוסט טכני מבוסס על מערכת שליחויות אמיתית שרצה בפרודקשן עם FastAPI, PostgreSQL, Celery ו-Redis.

---

## למה בוט WhatsApp ולא אפליקציה?

כשבונים מערכת שליחויות, האתגר הראשון הוא אימוץ משתמשים. אפליקציה דורשת הורדה, הרשמה, ולמידה של ממשק חדש. בוט WhatsApp? המשתמש כבר שם. הוא שולח הודעה ומתחיל לעבוד.

המערכת שלנו מנהלת את כל מחזור החיים של משלוח — משולח שיוצר הזמנה, דרך סדרן שמאשר, ועד שליח שאוסף ומוסר. הכל דרך שיחת WhatsApp.

---

## ארכיטקטורה כללית

```
WhatsApp Cloud API / WPPConnect
         │
         ▼
   Webhook Handler (FastAPI)
         │
         ▼
   State Machine (מנוע שיחה)
         │
         ▼
   Service Layer (לוגיקה עסקית)
         │
         ▼
   PostgreSQL ◄──► Celery + Redis
   (נתונים)        (משימות רקע)
```

הרעיון המרכזי: ה-webhook מקבל הודעה, ה-State Machine מחליט מה לעשות איתה, השירותים מבצעים את הלוגיקה, וההודעות החוזרות נשלחות באופן אסינכרוני דרך Celery.

---

## שכבה 1: קבלת הודעות — Webhook Handler

### מבנה הנתיב

```python
from fastapi import APIRouter, Depends, BackgroundTasks, Request

router = APIRouter()

@router.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    payload = await request.json()
    message = parse_whatsapp_payload(payload)

    # עיבוד ב-background — מחזירים 200 מיד
    background_tasks.add_task(process_message, db, message)
    return {"status": "ok"}
```

**למה `background_tasks`?** WhatsApp דורש תגובה מהירה (תוך 15 שניות). אם העיבוד ארוך — ה-webhook ייכשל ו-WhatsApp ישלח שוב. לכן מחזירים 200 מיד ומעבדים ברקע.

> **חשוב:** לעולם לא `asyncio.create_task()` — הוא בולע exceptions בשקט. תמיד `background_tasks.add_task()` של FastAPI.

### Idempotency — מניעת כפילויות

WhatsApp שולח את אותה הודעה כמה פעמים (retry). בלי הגנה, המשתמש יראה תגובה כפולה.

```python
async def _try_acquire_message(db: AsyncSession, message_id: str, platform: str) -> bool:
    """
    ניסיון אופטימיסטי לרכוש הודעה לעיבוד.
    מחזיר True אם ההודעה חדשה, False אם כפולה.
    """
    try:
        async with db.begin_nested():  # savepoint
            db.add(WebhookEvent(
                message_id=message_id,
                platform=platform,
                status="processing",
            ))
        await db.commit()
        return True
    except IntegrityError:
        pass  # הודעה כבר קיימת

    # בדיקה: אם ההודעה תקועה ב-processing יותר מ-2 דקות — מאפשרים retry
    row = await db.execute(
        select(WebhookEvent.status, WebhookEvent.created_at)
        .where(WebhookEvent.message_id == message_id)
    )
    # ...
```

**הדפוס:** INSERT אופטימיסטי → IntegrityError = כפילות → בדיקת staleness.
אם ההודעה תקועה ב-"processing" יותר מ-120 שניות, מאפשרים retry (הסשן הקודם כנראה קרס).

---

## שכבה 2: מנוע שיחה — State Machine

הלב של הבוט. כל משתמש נמצא ב-**state** מסוים, וכל הודעה שהוא שולח גורמת ל-**מעבר** ל-state הבא.

### הגדרת States כ-Enum

```python
class SenderState(str, Enum):
    INITIAL = "INITIAL"
    NEW = "SENDER.NEW"
    REGISTER_COLLECT_NAME = "SENDER.REGISTER.COLLECT_NAME"
    REGISTER_COLLECT_PHONE = "SENDER.REGISTER.COLLECT_PHONE"
    MENU = "SENDER.MENU"
    PICKUP_CITY = "SENDER.DELIVERY.PICKUP_CITY"
    PICKUP_STREET = "SENDER.DELIVERY.PICKUP_STREET"
    # ... המשך הזרימה
    DELIVERY_CONFIRM = "SENDER.DELIVERY.CONFIRM"
```

**למה `str, Enum`?** כי ה-state נשמר ב-DB כמחרוזת. `str, Enum` מאפשר השוואה ישירה: `state == "SENDER.MENU"`.

**קונבנציית שמות:** prefix לפי תפקיד (`SENDER.`, `COURIER.`, `DISPATCHER.`), ואז הזרימה (`DELIVERY.`, `REGISTER.`). זה מאפשר guard functions שבודקות `state.startswith("DISPATCHER.")`.

### מפת מעברים

```python
SENDER_TRANSITIONS = {
    SenderState.INITIAL: [
        SenderState.NEW,
        SenderState.REGISTER_COLLECT_NAME,
        SenderState.MENU,
    ],
    SenderState.MENU: [
        SenderState.PICKUP_CITY,
        SenderState.VIEW_DELIVERIES,
    ],
    SenderState.PICKUP_CITY: [SenderState.PICKUP_STREET],
    SenderState.PICKUP_STREET: [SenderState.PICKUP_NUMBER],
    # ...
}
```

**הכלל:** כל מעבר state חייב להיות מוגדר כאן. אם handler מחזיר state שלא מופיע ברשימה — `transition_to` ייכשל ויפעיל `force_state` עם warning. זה שומר על הזרימה מבוקרת.

### StateManager — ניהול מצב השיחה

```python
class StateManager:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_session(self, user_id: int, platform: str):
        """מביא סשן קיים או יוצר חדש"""
        session = await self.db.execute(
            select(ConversationSession).where(
                ConversationSession.user_id == user_id,
                ConversationSession.platform == platform,
            )
        )
        result = session.scalar_one_or_none()
        if not result:
            result = ConversationSession(
                user_id=user_id,
                platform=platform,
                current_state=SenderState.INITIAL.value,
            )
            self.db.add(result)
            await self.db.commit()
        return result

    async def transition_to(self, user_id, platform, new_state, context_update=None):
        """מעבר state עם ולידציה"""
        session = await self.get_or_create_session(user_id, platform)
        if self._is_valid_transition(session.current_state, new_state):
            session.current_state = new_state
            if context_update:
                session.context = {**session.context, **context_update}
            await self.db.commit()
            return True
        return False
```

**Context** — כל סשן מחזיק dict של context. למשל, כשהמשתמש מזין כתובת איסוף, היא נשמרת ב-context ומוצמדת למשלוח רק בשלב האישור. זה מאפשר חזרה אחורה בלי אובדן מידע.

### Handler Pattern — טיפול בהודעות

```python
class DispatcherStateHandler:
    async def handle_message(self, user, message, photo_file_id=None):
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        # שליחה ל-handler הספציפי לפי state
        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(user, message, context)

        # ניקוי context ביציאה מזרימה
        if new_state == DispatcherState.MENU.value:
            keys_to_clean = self._SHIPMENT_CONTEXT_KEYS | self._MANUAL_CHARGE_CONTEXT_KEYS
            # ...

        # מעבר state עם ולידציה
        if new_state != current_state:
            await self.state_manager.transition_to(user.id, platform, new_state, context_update)

        return response, new_state
```

**שלוש הפלטים של כל handler:**
1. `response` — ההודעה שתישלח למשתמש
2. `new_state` — ה-state הבא
3. `context_update` — מה לעדכן ב-context (למשל `{"pickup_city": "תל אביב"}`)

### Guard Functions — הגנה על זרימות רב-שלביות

```python
def _is_in_multi_step_flow(state: str) -> bool:
    """בדיקה אם המשתמש באמצע זרימה — מונע keyword hijacking"""
    return (
        state.startswith("DISPATCHER.ADD_SHIPMENT")
        or state.startswith("STATION.")
        or state.startswith("COURIER.REGISTER")
    )
```

**למה?** בלי guard, אם משתמש מזין כתובת כמו "תחנה מרכזית" באמצע יצירת משלוח — המילה "תחנה" עלולה להפעיל תגובת שיווק במקום להתקבל ככתובת. ה-guard מוודא שבזמן זרימה רב-שלבית, רק ה-handler הרלוונטי מטפל בהודעה.

---

## שכבה 3: לוגיקה עסקית — Service Layer

### Transactional Outbox — הודעות אמינות

הבעיה: מה קורה כשעדכנת את ה-DB בהצלחה אבל שליחת ההודעה ל-WhatsApp נכשלה? המשתמש לא יודע שהפעולה הצליחה.

הפתרון: **Transactional Outbox Pattern** — ההודעה נשמרת בטבלת outbox **באותה טרנזקציה** עם הפעולה העסקית.

```python
class OutboxService:
    async def queue_message(self, platform, recipient_id, message_type, content):
        """שומר הודעה בטבלת outbox — באותה טרנזקציה עם הפעולה העסקית"""
        outbox_msg = OutboxMessage(
            platform=platform,
            recipient_id=recipient_id,
            message_type=message_type,
            content=content,
            status=MessageStatus.PENDING,
        )
        self.db.add(outbox_msg)
        # לא עושים commit כאן — ה-commit קורה עם הפעולה העסקית
```

**הזרימה:**
1. פעולה עסקית (למשל: תפיסת משלוח) + הכנסת הודעה ל-outbox → **commit אטומי**
2. Celery worker שולף הודעות pending → שולח ל-WhatsApp
3. הצלחה → סימון sent / כישלון → retry עם exponential backoff

```python
def _calculate_backoff_seconds(retry_count, base_seconds, max_backoff_seconds):
    """Exponential backoff: 2s, 4s, 8s, 16s... עד שעה"""
    backoff = base_seconds * (2 ** retry_count)
    return min(backoff, max_backoff_seconds)
```

### Atomic Capture — תפיסת משלוח בטוחה

כשעשרות שליחים רואים אותו משלוח ולוחצים "תפוס" בו-זמנית, רק אחד צריך להצליח. בלי נעילה — race condition, חיובים כפולים, כאוס.

```python
class CaptureService:
    async def capture_delivery(self, courier_id: int, delivery_id: int):
        # 1. נעילת שורת המשלוח
        delivery = await self.db.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .with_for_update()  # FOR UPDATE — נעילת שורה
        )

        # 2. בדיקת סטטוס
        if delivery.status != DeliveryStatus.OPEN:
            raise DeliveryAlreadyCapturedError(delivery_id)

        # 3. נעילת ארנק השליח
        wallet = await self.db.execute(
            select(CourierWallet)
            .where(CourierWallet.courier_id == courier_id)
            .with_for_update()
        )

        # 4. בדיקת יתרה
        if wallet.balance - delivery.fee < wallet.credit_limit:
            raise InsufficientCreditError()

        # 5. עדכון אטומי
        delivery.status = DeliveryStatus.CAPTURED
        delivery.courier_id = courier_id
        wallet.balance -= delivery.fee

        # 6. הודעה ל-outbox (אותה טרנזקציה!)
        await self.outbox.queue_capture_notification(delivery)

        await self.db.commit()
```

> **מלכודת:** אסור `joinedload` עם `with_for_update()` — PostgreSQL דוחה `FOR UPDATE` על `LEFT OUTER JOIN`. צריך לפצל לשתי שאילתות.

---

## שכבה 4: תקשורת עם WhatsApp — Provider Pattern

### ממשק אחיד — Dependency Inversion

במקום להיות תלויים ב-API ספציפי, הגדרנו ממשק בסיסי:

```python
class BaseWhatsAppProvider(ABC):
    @abstractmethod
    async def send_text(self, to: str, text: str, keyboard=None) -> None:
        """שליחת הודעת טקסט עם כפתורים אופציונליים"""

    @abstractmethod
    async def send_media(self, to: str, media_url: str, media_type: str) -> None:
        """שליחת תמונה/מסמך"""

    @abstractmethod
    def format_text(self, html_text: str) -> str:
        """המרת HTML → פורמט WhatsApp (*bold*, _italic_)"""

    @abstractmethod
    def normalize_phone(self, phone: str) -> str:
        """נרמול טלפון לפורמט E.164"""
```

**שני מימושים:**
- **WPPConnectProvider** — gateway מבוסס Node.js (WPPConnect)
- **PywaProvider** — Meta Cloud API (הרשמי)

**Hybrid Mode** — שילוב של שניהם: Cloud API לצ'אטים פרטיים, WPPConnect לקבוצות (Cloud API לא תומך בקבוצות לא-רשמיות).

### Retry עם Exponential Backoff

```python
async def _request_with_retry(self, endpoint, payload, operation_name):
    for attempt in range(self._max_retries):
        response = await client.post(
            f"{self._gateway_url}/{endpoint}",
            json=payload,
        )

        if response.status_code == 200:
            return

        # שגיאות זמניות — retry
        if response.status_code in (502, 503, 504, 429):
            backoff = 2 ** attempt
            logger.warning("שגיאה זמנית, ממתין...", extra_data={
                "phone": PhoneNumberValidator.mask(payload.get("phone")),
                "status_code": response.status_code,
                "backoff_seconds": backoff,
            })
            await asyncio.sleep(backoff)
            continue

        raise WhatsAppError(...)
```

### Circuit Breaker — הגנה מפני כשלון מדורג

כש-WhatsApp API נופל, אנחנו לא רוצים לשלוח אלפי בקשות שנידונו לכישלון. Circuit Breaker חוסם קריאות כשהשירות למטה.

```python
class CircuitBreaker:
    """
    CLOSED → שגיאות מצטברות → OPEN (חוסם הכל)
    OPEN → timeout עבר → HALF_OPEN (מנסה בזהירות)
    HALF_OPEN → הצלחות → CLOSED (חזרה לנורמלי)
    """
    _instances: dict[str, "CircuitBreaker"] = {}  # singleton לכל שירות

    @classmethod
    def get_instance(cls, service_name: str) -> "CircuitBreaker":
        if service_name not in cls._instances:
            cls._instances[service_name] = cls(
                name=service_name,
                config=CircuitBreakerConfig(
                    failure_threshold=5,    # 5 כשלונות → OPEN
                    timeout_seconds=30.0,   # ניסיון אחרי 30 שניות
                    success_threshold=2,    # 2 הצלחות → CLOSED
                ),
            )
        return cls._instances[service_name]
```

---

## אבטחה ו-ולידציה

### ולידציית קלט — כל הודעה עוברת סניטציה

```python
class TextSanitizer:
    @staticmethod
    def sanitize(text: str, max_length: int = 1000) -> str:
        """strip, truncate, הסרת null bytes, כיווץ רווחים"""

    @staticmethod
    def check_for_injection(text: str) -> tuple[bool, str | None]:
        """בדיקת SQL injection, XSS, command injection"""
        # דפוסים: OR 1=1, UNION SELECT, <script>, javascript:, onclick=
```

### מיסוך מספרי טלפון בלוגים

```python
class PhoneNumberValidator:
    @staticmethod
    def mask(phone: str) -> str:
        """'+972501234567' → '+97250123****'"""

# שימוש בכל הלוגים
logger.info("הודעה נשלחה", extra_data={
    "phone": PhoneNumberValidator.mask(phone)  # לעולם לא המספר המלא
})
```

### אימות Webhook — חתימת HMAC

```python
def verify_cloud_api_signature(request: Request, body: bytes) -> bool:
    """אימות שההודעה באמת הגיעה מ-Meta"""
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = hmac.new(
        settings.WHATSAPP_CLOUD_API_APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

---

## Middleware Stack — שכבות הגנה

```python
# סדר חשוב — חיצוני לפנימי:
app.add_middleware(SecurityHeadersMiddleware)      # HSTS, CSP
app.add_middleware(CorrelationIdMiddleware)         # מזהה ייחודי לכל בקשה
app.add_middleware(RequestLoggingMiddleware)        # לוגים עם מיסוך PII
app.add_middleware(WebhookRateLimitMiddleware)      # 100 req/60s per IP
```

**Correlation ID** — כל בקשה מקבלת מזהה ייחודי שעובר דרך כל השכבות. כשיש באג בפרודקשן, אפשר לעקוב אחרי בקשה אחת מה-webhook ועד ל-DB.

---

## ניהול כפתורים ותצוגה

### כפתורי מקלדת

```python
class MessageResponse:
    def __init__(self, text: str, keyboard: list[list[str]] | None = None):
        self.text = text
        self.keyboard = keyboard  # [["כן", "לא"], ["ביטול"]]
```

**מלכודת קלאסית:** כפתורים הם plain text. אם תעשה `html.escape()` על טקסט כפתור, המשתמש יראה `&amp;` במקום `&`. ה-escape נדרש רק בגוף ההודעה עם `parse_mode=HTML`.

### התאמת פורמט בין פלטפורמות

```python
def convert_html_to_whatsapp(text: str) -> str:
    """HTML → WhatsApp Markdown"""
    # <b>bold</b>     → *bold*
    # <i>italic</i>   → _italic_
    # <s>strike</s>   → ~strike~
    # <code>code</code> → `code`
```

---

## תבניות נוספות שכדאי לדעת

### ניתוב לפי תפקיד — כיסוי מלא

```python
# ❌ לא נכון — else תופס תפקידים לא צפויים
if user.role == UserRole.COURIER:
    handler = CourierStateHandler(db)
else:
    handler = SenderStateHandler(db)  # ADMIN? STATION_OWNER? 🤷

# ✅ נכון — מפורש לכל תפקיד
if user.role == UserRole.COURIER:
    handler = CourierStateHandler(db)
elif user.role == UserRole.SENDER:
    handler = SenderStateHandler(db)
elif user.role == UserRole.DISPATCHER:
    handler = DispatcherStateHandler(db)
elif user.role == UserRole.STATION_OWNER:
    handler = StationOwnerStateHandler(db)
else:
    logger.warning("תפקיד לא מוכר", extra_data={"role": str(user.role)})
```

### ניקוי Context ביציאה מזרימה

```python
# ❌ context ישן נשאר — עלול לגרום לפעולה על נתונים מיושנים
return response, State.MENU.value, {}

# ✅ ניקוי מפורש
return response, State.MENU.value, {
    "pickup_city": None,
    "pickup_street": None,
    "selected_delivery": None,
}
```

### בדיקת None מפורשת

```python
# ❌ 0 הוא falsy — קואורדינטה 0 תיעלם
if validated.latitude:
    latitude = Decimal(str(validated.latitude))

# ✅ בדיקת None מפורשת
if validated.latitude is not None:
    latitude = Decimal(str(validated.latitude))
```

---

## מבנה הפרויקט

```
app/
├── api/webhooks/           # Webhook handlers (WhatsApp, Telegram)
├── core/
│   ├── config.py           # הגדרות (Pydantic Settings)
│   ├── validation.py       # ולידטורים + סניטציה
│   ├── exceptions.py       # שגיאות מותאמות עם קודים
│   ├── circuit_breaker.py  # הגנה על שירותים חיצוניים
│   └── middleware.py       # Correlation ID, Rate Limit, Logging
├── db/models/              # SQLAlchemy models
├── domain/services/
│   ├── whatsapp/           # Provider pattern (base + implementations)
│   ├── outbox_service.py   # Transactional Outbox
│   ├── capture_service.py  # תפיסת משלוח אטומית
│   └── wallet_service.py   # ארנק עם נעילת שורה
├── state_machine/
│   ├── states.py           # State enums + מפת מעברים
│   ├── manager.py          # ניהול state ו-context
│   └── handlers.py         # Handler לכל תפקיד
└── workers/tasks.py        # Celery workers
```

---

## סיכום — עקרונות מנחים

1. **State Machine עם מעברים מוגדרים** — שומר על הזרימה מבוקרת ומונע מצבים בלתי אפשריים
2. **Transactional Outbox** — מבטיח שהודעות לא יאבדו גם אם WhatsApp API נופל
3. **Idempotency** — מונע עיבוד כפול של הודעות (WhatsApp שולח retries)
4. **Circuit Breaker** — מגן מפני כשלון מדורג כש-API חיצוני למטה
5. **Provider Pattern** — מאפשר החלפת ספק WhatsApp בלי לשנות לוגיקה עסקית
6. **Row-level Locking** — מונע race conditions בפעולות פיננסיות
7. **Context Cleanup** — מנקה state ישן כשיוצאים מזרימה
8. **Guard Functions** — מגן על זרימות רב-שלביות מפני keyword hijacking
9. **PII Masking** — לעולם לא מספר טלפון מלא בלוגים
10. **Correlation IDs** — מעקב אחרי בקשה מקצה לקצה

הבוט הזה רץ בפרודקשן, מטפל במאות משלוחים ביום, ותומך בו-זמנית ב-WhatsApp ו-Telegram. הארכיטקטורה הזו עובדת — ועכשיו אתם יודעים איך לבנות אחת כזו.

---

*נכתב על בסיס מערכת שליחויות אמיתית. קוד מפושט לצורך הדגמה.*
