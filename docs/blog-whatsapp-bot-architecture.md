# איך לבנות בוט WhatsApp שעובד כמו מוצר אמיתי

> הלקחים, הדפוסים והמלכודות מבניית בוט WhatsApp שרץ בפרודקשן — עם דוגמאות קוד ב-Python ו-FastAPI.

---

## למה בוט WhatsApp?

אפליקציה דורשת הורדה, הרשמה, ולמידה של ממשק חדש. בוט WhatsApp? המשתמש כבר שם. הוא שולח הודעה ומתחיל לעבוד. אין חיכוך, אין התקנה, אין onboarding.

אבל "בוט פשוט שמגיב להודעות" ו"מוצר שרץ בפרודקשן" הם שני דברים שונים לגמרי. הפוסט הזה מכסה את הפער ביניהם.

---

## הארכיטקטורה — מבט על

```
WhatsApp API (Cloud / Gateway)
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

ה-webhook מקבל הודעה, ה-State Machine מחליט באיזה שלב של השיחה נמצא המשתמש, השירותים מבצעים את הלוגיקה, וההודעות החוזרות נשלחות אסינכרונית. פשוט ברעיון, מורכב בביצוע.

---

## 1. קבלת הודעות — Webhook Handler

### הבעיה הראשונה: WhatsApp לא מחכה

WhatsApp מצפה לתגובת 200 תוך 15 שניות. אם לא — הוא שולח שוב. ושוב. לכן הכלל הראשון:

```python
@router.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    payload = await request.json()
    message = parse_payload(payload)

    # מחזירים 200 מיד, מעבדים ברקע
    background_tasks.add_task(process_message, db, message)
    return {"status": "ok"}
```

> **מלכודת:** לעולם לא `asyncio.create_task()` בתוך webhook. הוא בולע exceptions בשקט ואי אפשר לדעת שמשהו נכשל. תמיד `background_tasks.add_task()` של FastAPI.

### מניעת כפילויות — Idempotency

WhatsApp שולח retries. בלי הגנה, המשתמש יקבל תגובה כפולה. הפתרון: טבלת `webhook_events` ב-DB עם INSERT אופטימיסטי:

```python
async def _try_acquire_message(db: AsyncSession, message_id: str) -> bool:
    """מחזיר True אם ההודעה חדשה, False אם כפולה"""
    try:
        async with db.begin_nested():
            db.add(WebhookEvent(
                message_id=message_id,
                status="processing",
            ))
        await db.commit()
        return True  # הודעה חדשה — מעבדים
    except IntegrityError:
        pass  # הודעה כבר קיימת

    # בדיקה: אולי ההודעה תקועה ב-processing (הסשן קרס)?
    row = await db.execute(
        select(WebhookEvent).where(WebhookEvent.message_id == message_id)
    )
    event = row.scalar_one_or_none()
    if event and event.status == "completed":
        return False  # כבר טופלה — דילוג

    # תקועה יותר מ-2 דקות? מאפשרים retry
    if (now() - event.created_at).seconds > 120:
        return True
    return False
```

**למה DB ולא cache?** כי cache נעלם בריסטארט, לא משותף בין workers, ולא שורד כשלונות. DB נותן idempotency אמיתי.

### אימות חתימה — לוודא שההודעה מ-WhatsApp

```python
def verify_signature(request: Request, body: bytes) -> bool:
    """HMAC-SHA256 — מוודא שה-webhook הגיע מ-Meta"""
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = hmac.new(
        settings.APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

בלי אימות, כל אחד יכול לשלוח בקשות ל-webhook שלכם ולגרום לבוט לבצע פעולות.

---

## 2. מנוע השיחה — State Machine

הלב של כל בוט שעושה יותר מלענות על שאלות. כל משתמש נמצא ב-**state** — ה-state קובע מה קורה עם ההודעה הבאה.

### הגדרת States

```python
class UserState(str, Enum):
    INITIAL = "INITIAL"
    NEW = "USER.NEW"

    # רישום
    REGISTER_NAME = "USER.REGISTER.NAME"
    REGISTER_PHONE = "USER.REGISTER.PHONE"

    # תפריט ראשי
    MENU = "USER.MENU"

    # טופס מרובה שלבים
    FORM_STEP_1 = "USER.FORM.STEP_1"
    FORM_STEP_2 = "USER.FORM.STEP_2"
    FORM_STEP_3 = "USER.FORM.STEP_3"
    FORM_CONFIRM = "USER.FORM.CONFIRM"
```

**למה `str, Enum`?** ה-state נשמר ב-DB כמחרוזת. `str, Enum` מאפשר השוואה ישירה (`state == "USER.MENU"`) וגם type safety.

**קונבנציית שמות:** prefix לפי תפקיד (`USER.`, `ADMIN.`), ואז הזרימה (`REGISTER.`, `FORM.`). זה מאפשר guard functions שבודקות `state.startswith("USER.FORM.")` — לדעת בקלות אם המשתמש באמצע טופס.

### מפת מעברים — מה מותר

```python
TRANSITIONS = {
    UserState.INITIAL: [UserState.NEW, UserState.REGISTER_NAME, UserState.MENU],
    UserState.REGISTER_NAME: [UserState.REGISTER_PHONE, UserState.MENU],
    UserState.REGISTER_PHONE: [UserState.MENU],
    UserState.MENU: [UserState.FORM_STEP_1],
    UserState.FORM_STEP_1: [UserState.FORM_STEP_2],
    UserState.FORM_STEP_2: [UserState.FORM_STEP_3],
    UserState.FORM_STEP_3: [UserState.FORM_CONFIRM],
    UserState.FORM_CONFIRM: [UserState.MENU],
}
```

כל מעבר state **חייב** להיות מוגדר מראש. אם handler מנסה לעבור ל-state שלא ברשימה — `transition_to` מחזיר `False`. זה מונע באגים שקטים שבהם המשתמש מגיע למצב בלתי אפשרי.

### StateManager — שמירת המצב

```python
class StateManager:
    async def get_or_create_session(self, user_id: int, platform: str):
        """מחזיר סשן קיים או יוצר חדש"""
        result = await self.db.execute(
            select(ConversationSession).where(
                ConversationSession.user_id == user_id,
                ConversationSession.platform == platform,
            )
        )
        session = result.scalar_one_or_none()
        if not session:
            session = ConversationSession(
                user_id=user_id,
                platform=platform,
                current_state=UserState.INITIAL.value,
            )
            self.db.add(session)
            await self.db.commit()
        return session

    async def transition_to(self, user_id, platform, new_state, context_update=None):
        """מעבר state — רק אם מותר"""
        session = await self.get_or_create_session(user_id, platform)
        if not self._is_valid_transition(session.current_state, new_state):
            return False
        session.current_state = new_state
        if context_update:
            context = copy.deepcopy(session.context_data or {})
            context.update(context_update)
            session.context_data = context
        await self.db.commit()
        return True
```

**Context** — כל סשן מחזיק `dict` של context. כשמשתמש ממלא טופס בשלושה שלבים, כל שלב שומר את הנתונים ב-context, ורק בשלב האישור הכל נכתב ל-DB. זה מאפשר ביטול באמצע בלי שנשאר זבל ב-DB.

> **טיפ:** SQLAlchemy לא מזהה שינויים בעמודות JSON. חובה `copy.deepcopy` לפני update, אחרת השינוי לא יישמר.

### Handler — הדפוס המרכזי

```python
async def handle_message(self, user, message, context):
    handler = self._get_handler(current_state)  # dispatch לפי state
    response, new_state, context_update = await handler(user, message, context)

    # ניקוי context ביציאה מזרימה
    if new_state == UserState.MENU.value and self._is_form_flow(current_state):
        context_update = {
            "form_field_1": None,
            "form_field_2": None,
            "form_field_3": None,
        }

    await self.state_manager.transition_to(user.id, platform, new_state, context_update)
    return response
```

**שלוש הפלטים של כל handler:**
1. `response` — מה לשלוח למשתמש
2. `new_state` — לאיזה state לעבור
3. `context_update` — מה לשמור ב-context

**ניקוי context** — כשמשתמש חוזר לתפריט הראשי, חובה לנקות את ה-context של הזרימה הקודמת. בלי ניקוי, context ישן עלול לגרום לפעולה על נתונים מיושנים בפעם הבאה.

### Guard Functions — הגנה על טפסים רב-שלביים

```python
def _is_in_multi_step_flow(state: str) -> bool:
    """האם המשתמש באמצע טופס?"""
    return (
        state.startswith("USER.FORM.")
        or state.startswith("USER.REGISTER.")
    )
```

**למה?** נניח שיש לכם keyword trigger: כשמשתמש כותב "עזרה" — הבוט מציג הוראות. אבל מה אם המשתמש באמצע טופס ושם שדה הוא "מרכז עזרה"? בלי guard, המילה "עזרה" תפעיל את ה-trigger במקום להתקבל כקלט. Guard functions מוודאות שבזמן זרימה רב-שלבית, רק ה-handler של אותה זרימה מטפל בהודעה.

```python
# ❌ לא נכון — תופס מילות מפתח גם באמצע טופס
if "עזרה" in text:
    return help_response()

# ✅ נכון — בודקים קודם אם באמצע זרימה
if not _is_in_multi_step_flow(current_state):
    if "עזרה" in text:
        return help_response()
```

---

## 3. שליחת הודעות — Provider Pattern

### ממשק אחיד

יום אחד אתם משתמשים ב-Cloud API של Meta, למחרת אתם רוצים WPPConnect, ואולי מחר ספק אחר. אם הקוד שלכם קשור לספק ספציפי — אתם בבעיה.

```python
class BaseWhatsAppProvider(ABC):
    @abstractmethod
    async def send_text(self, to: str, text: str, keyboard=None) -> None:
        """שליחת טקסט עם כפתורים אופציונליים"""

    @abstractmethod
    async def send_media(self, to: str, media_url: str, media_type: str = "image") -> None:
        """שליחת תמונה/מסמך/וידאו"""

    @abstractmethod
    def format_text(self, html_text: str) -> str:
        """המרת HTML → פורמט הספק (*bold*, _italic_)"""

    @abstractmethod
    def normalize_phone(self, phone: str) -> str:
        """נרמול לפורמט E.164: 0501234567 → +972501234567"""
```

השירותים העסקיים תלויים רק בממשק. החלפת ספק = מימוש חדש של הממשק, אפס שינויים בלוגיקה.

### Retry — לא כל כשלון הוא סופי

```python
async def _send_with_retry(self, endpoint: str, payload: dict):
    for attempt in range(self._max_retries):
        response = await client.post(
            f"{self._gateway_url}/{endpoint}",
            json=payload,
        )

        if response.status_code == 200:
            return

        # שגיאות זמניות — שווה לנסות שוב
        if response.status_code in (429, 502, 503, 504):
            backoff = 2 ** attempt  # 1s, 2s, 4s
            logger.warning("שגיאה זמנית, ממתין", extra_data={
                "status_code": response.status_code,
                "retry_in": backoff,
            })
            await asyncio.sleep(backoff)
            continue

        # שגיאה קבועה — אין טעם לנסות שוב
        raise WhatsAppError(f"Failed: {response.status_code}")
```

**429 (Rate Limit)** — WhatsApp מגביל כמות הודעות. Exponential backoff נותן ל-API לנשום.

### Circuit Breaker — הגנה מפני כשלון מדורג

כש-WhatsApp API למטה, אלפי הודעות ממתינות לשליחה. בלי הגנה, כולן ינסו, ייכשלו, ינסו שוב — ויציפו את השרת.

```python
class CircuitBreaker:
    """
    שלושה מצבים:
    CLOSED  → עובד רגיל, סופר כשלונות
    OPEN    → API למטה, חוסם הכל (מונע הצפה)
    HALF_OPEN → מנסה בקשה אחת לבדוק אם השירות חזר
    """
    @classmethod
    def get_instance(cls, service_name: str) -> "CircuitBreaker":
        """Singleton לכל שירות — Telegram ו-WhatsApp נפרדים"""
        if service_name not in cls._instances:
            cls._instances[service_name] = cls(
                name=service_name,
                config=CircuitBreakerConfig(
                    failure_threshold=5,     # 5 כשלונות רצופים → OPEN
                    timeout_seconds=30.0,    # אחרי 30 שניות → HALF_OPEN
                    success_threshold=2,     # 2 הצלחות → CLOSED
                ),
            )
        return cls._instances[service_name]

    async def execute(self, func, *args, **kwargs):
        if self._state == CircuitState.OPEN:
            if self._should_try_half_open():
                self._state = CircuitState.HALF_OPEN
            else:
                raise CircuitBreakerOpenError(self._name)

        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            raise
```

---

## 4. Transactional Outbox — הודעות שלא הולכות לאיבוד

### הבעיה

מה קורה כשפעולה עסקית הצליחה (נשמרה ב-DB) אבל שליחת ההודעה ל-WhatsApp נכשלה? המשתמש לא יודע שהפעולה בוצעה.

### הפתרון

ההודעה נשמרת בטבלת outbox **באותה טרנזקציה** עם הפעולה העסקית:

```python
class OutboxService:
    async def queue_message(self, recipient_id, content, platform="whatsapp"):
        """ההודעה נכנסת ל-outbox באותה טרנזקציה עם הפעולה העסקית"""
        self.db.add(OutboxMessage(
            platform=platform,
            recipient_id=recipient_id,
            content=content,
            status=MessageStatus.PENDING,
        ))
        # אין commit כאן — ה-commit קורה יחד עם הפעולה העסקית
```

**הזרימה:**
```
1. פעולה עסקית + הכנסת הודעה ל-outbox → commit אטומי
2. Celery worker שולף הודעות pending
3. שולח ל-WhatsApp
4. הצלחה → סימון sent
5. כישלון → retry עם exponential backoff (2s, 4s, 8s... עד שעה)
6. מיצוי retries → Dead Letter Queue (לטיפול ידני)
```

```python
def _calculate_backoff(retry_count: int, base: int = 2, max_seconds: int = 3600) -> int:
    """Exponential backoff עם תקרה"""
    return min(base * (2 ** retry_count), max_seconds)
```

**למה לא לשלוח ישירות?** כי זה מעכב את התגובה למשתמש, ואם השליחה נכשלת אחרי שה-DB כבר עודכן — אין דרך לדעת. Outbox מבטיח: אם הפעולה נשמרה, ההודעה **תישלח** — גם אם לוקח כמה ניסיונות.

---

## 5. פעולות מקביליות — Row-Level Locking

כשכמה משתמשים מנסים לבצע את אותה פעולה בו-זמנית (למשל: תפיסת פריט, עדכון יתרה), צריך נעילה:

```python
async def claim_item(self, user_id: int, item_id: int):
    # 1. נעילת שורה — רק אחד יכול לגעת בה
    item = await self.db.execute(
        select(Item)
        .where(Item.id == item_id)
        .with_for_update()  # FOR UPDATE
    )

    # 2. בדיקת סטטוס
    if item.status != "available":
        raise AlreadyClaimedError(item_id)

    # 3. עדכון אטומי
    item.status = "claimed"
    item.claimed_by = user_id

    # 4. הודעה ב-outbox (אותה טרנזקציה!)
    await self.outbox.queue_message(
        recipient_id=user_id,
        content={"text": "הפריט שלך!"},
    )

    await self.db.commit()  # הכל ביחד, או כלום
```

> **מלכודת PostgreSQL:** אסור `joinedload()` עם `with_for_update()`. PostgreSQL דוחה `FOR UPDATE` על `LEFT OUTER JOIN` שנוצר מ-joinedload. הפתרון: שאילתה ראשונה עם נעילה, שאילתה שנייה לטעינת קשרים.

---

## 6. אבטחה — הדברים שמחכים לכם בפרודקשן

### ולידציית קלט

משתמשים שולחים הכל. SQL injection, XSS, טקסט עם null bytes. כל קלט חייב לעבור סניטציה:

```python
class TextSanitizer:
    @staticmethod
    def sanitize(text: str, max_length: int = 1000) -> str:
        """strip, חיתוך, הסרת null bytes, כיווץ רווחים"""

    @staticmethod
    def check_for_injection(text: str) -> tuple[bool, str | None]:
        """סריקת SQL injection, XSS, command injection"""
        # OR 1=1, UNION SELECT, <script>, javascript:, onclick=
```

### מיסוך PII בלוגים

מספרי טלפון הם PII (Personally Identifiable Information). אסור שיופיעו בלוגים:

```python
class PhoneNumberValidator:
    @staticmethod
    def mask(phone: str) -> str:
        """'+972501234567' → '+97250123****'"""

# ❌ לעולם לא
logger.info(f"הודעה נשלחה ל-{phone}")

# ✅ תמיד
logger.info("הודעה נשלחה", extra_data={
    "phone": PhoneNumberValidator.mask(phone)
})
```

### Rate Limiting על Webhooks

```python
class WebhookRateLimitMiddleware:
    """Sliding window: 100 בקשות ל-60 שניות, לפי IP"""
    # מחזיר 429 + Retry-After header + correlation ID
```

בלי rate limiting, תוקף יכול להציף את ה-webhook שלכם ולגרום ל-DoS.

---

## 7. Middleware Stack

```python
app.add_middleware(SecurityHeadersMiddleware)     # HSTS, CSP
app.add_middleware(CorrelationIdMiddleware)        # מזהה ייחודי לכל בקשה
app.add_middleware(RequestLoggingMiddleware)       # לוגים עם מיסוך PII
app.add_middleware(WebhookRateLimitMiddleware)     # הגנה מפני הצפה
```

**Correlation ID** — כל בקשה מקבלת מזהה ייחודי שעובר דרך כל השכבות. כשיש באג בפרודקשן ומשתמש מדווח "ההודעה לא הגיעה", אתם מחפשים את ה-correlation ID בלוגים ורואים בדיוק מה קרה — מה-webhook, דרך ה-state machine, ועד לניסיון השליחה.

```python
# אוטומטי בכל log
{
    "timestamp": "2026-03-14T10:30:00Z",
    "level": "ERROR",
    "correlation_id": "a1b2c3d4",
    "message": "שליחה נכשלה",
    "extra": {"phone": "+97250123****", "retry": 3}
}
```

---

## 8. כפתורים ותצוגה — המלכודות

### כפתורים הם plain text

```python
class MessageResponse:
    def __init__(self, text: str, keyboard: list[list[str]] | None = None):
        self.text = text
        self.keyboard = keyboard  # [["אישור", "ביטול"], ["חזור"]]
```

**מלכודת:** כפתורי WhatsApp/Telegram הם plain text. אם תעשו `html.escape()` על טקסט כפתור, המשתמש יראה `&amp;` במקום `&`. ה-escape נדרש רק בגוף ההודעה.

```python
# ❌ html entities בכפתור
keyboard.append([f"📦 {html.escape(item_name)}"])  # מציג: Ben &amp; Jerry's

# ✅ טקסט רגיל בכפתור
keyboard.append([f"📦 {item_name}"])  # מציג: Ben & Jerry's
```

### חילוץ בחירה מכפתור — עיגון regex

כשמשתמש לוחץ כפתור, הטקסט של הכפתור חוזר כהודעה. צריך לחלץ ממנו את הבחירה:

```python
# ❌ תופס כל מספר — גם בטקסט חופשי
match = re.search(r"(\d+)", text)

# ✅ מעוגן לפורמט הכפתור
match = re.match(r"📦\s*(\d+)\.", text)
```

### המרת פורמט בין פלטפורמות

אם הבוט תומך גם ב-Telegram — הפורמט שונה:

```python
def convert_html_to_whatsapp(text: str) -> str:
    """Telegram HTML → WhatsApp Markdown"""
    # <b>bold</b>     → *bold*
    # <i>italic</i>   → _italic_
    # <s>strike</s>   → ~strike~
    # <code>code</code> → `code`
```

---

## 9. ניתוב לפי תפקידים

כשיש כמה סוגי משתמשים (לקוח, מנהל, ספק), הניתוב חייב להיות מפורש:

```python
# ❌ else גנרי — מה קורה עם תפקיד חדש?
if user.role == "admin":
    handler = AdminHandler(db)
else:
    handler = UserHandler(db)  # גם manager? גם support?

# ✅ מפורש — כל תפקיד מטופל
if user.role == "admin":
    handler = AdminHandler(db)
elif user.role == "user":
    handler = UserHandler(db)
elif user.role == "manager":
    handler = ManagerHandler(db)
else:
    logger.warning("תפקיד לא מוכר", extra_data={"role": user.role})
    return unknown_role_response()
```

כשמוסיפים תפקיד חדש ושוכחים לעדכן `else` — הבאג שקט ומתגלה רק כשמשתמש עם התפקיד החדש מדווח שמשהו לא עובד.

---

## 10. בדיקת None — המלכודת הכי שקטה

```python
# ❌ 0 הוא falsy — ערכים לגיטימיים נעלמים
if user.latitude:
    save_location(user.latitude)  # קואורדינטה 0 = לא נשמר!

if price:
    apply_discount(price)  # מחיר 0 = לא מופעל!

# ✅ בדיקת None מפורשת
if user.latitude is not None:
    save_location(user.latitude)

if price is not None:
    apply_discount(price)
```

כלל: בכל ערך מספרי שאפס הוא ערך תקין — `is not None`, לא `if value`.

---

## מבנה פרויקט מומלץ

```
app/
├── api/webhooks/            # קבלת הודעות
├── core/
│   ├── config.py            # הגדרות (Pydantic Settings)
│   ├── validation.py        # ולידציה + סניטציה
│   ├── exceptions.py        # שגיאות מותאמות עם קודים
│   ├── circuit_breaker.py   # הגנה על APIs חיצוניים
│   └── middleware.py        # Correlation ID, Rate Limit, Logging
├── db/models/               # מודלים (SQLAlchemy)
├── domain/services/
│   ├── messaging/           # Provider pattern לשליחת הודעות
│   └── outbox_service.py    # Transactional Outbox
├── state_machine/
│   ├── states.py            # State enums + מפת מעברים
│   ├── manager.py           # ניהול state ו-context
│   └── handlers.py          # Handler לכל זרימה
└── workers/tasks.py         # Celery workers לעיבוד רקע
```

---

## סיכום — 10 עקרונות לבוט WhatsApp שעובד

1. **תחזיר 200 מיד** — WhatsApp לא מחכה. עבד ברקע
2. **Idempotency** — WhatsApp שולח retries. מנע כפילויות ב-DB
3. **State Machine** — כל שיחה היא רצף מצבים. הגדר מעברים מותרים
4. **Guard Functions** — הגן על טפסים רב-שלביים מפני keyword hijacking
5. **ניקוי Context** — כשחוזרים לתפריט, נקו את ה-context של הזרימה הקודמת
6. **Transactional Outbox** — הודעות לא הולכות לאיבוד, גם כשה-API למטה
7. **Circuit Breaker** — כשספק למטה, הפסיקו לנסות. נסו שוב אחרי timeout
8. **Provider Pattern** — אל תהיו תלויים בספק ספציפי. ממשק + מימושים
9. **Row Locking** — פעולות מקביליות חייבות נעילת שורה, לא תקוות
10. **PII Masking** — לעולם לא מספר טלפון בלוגים

הדפוסים האלה לא ייחודיים ל-WhatsApp — הם רלוונטיים לכל בוט שרץ בפרודקשן, בכל פלטפורמה. אבל ב-WhatsApp, בגלל ה-retries, ה-rate limits וההגבלות של ה-API — הם הופכים מ-nice to have ל-must have.

---

*מבוסס על לקחים ממערכת פרודקשן אמיתית. קוד מפושט לצורך הדגמה.*
