# דוח קוד ריוויו — Shipment Bot

> תאריך: 2026-02-13
> סקירה מקיפה של כל שכבות המערכת עם הצעות לשיפור

---

## תוכן עניינים

1. [סיכום מנהלים](#סיכום-מנהלים)
2. [ממצאים קריטיים](#ממצאים-קריטיים)
3. [שכבת מסד הנתונים](#שכבת-מסד-הנתונים)
4. [שכבת ה-Domain ושירותים](#שכבת-ה-domain-ושירותים)
5. [שכבת ה-API ו-Webhooks](#שכבת-ה-api-ו-webhooks)
6. [מכונת מצבים (State Machine)](#מכונת-מצבים-state-machine)
7. [תשתית Core](#תשתית-core)
8. [בדיקות](#בדיקות)
9. [אבטחה](#אבטחה)
10. [ביצועים](#ביצועים)
11. [סיכום הצעות לשיפור](#סיכום-הצעות-לשיפור)

---

## סיכום מנהלים

הפרויקט מדגים ארכיטקטורה מוצקה עם הפרדת שכבות ברורה, תבניות עיצוב מתקדמות (Transactional Outbox, Circuit Breaker, State Machine), ומערכת בדיקות מקיפה (~962 טסטים). הקוד עומד ברוב הסטנדרטים שהוגדרו ב-`CLAUDE.md`.

### נקודות חזקות
- ארכיטקטורה מרובדת ומסודרת עם הפרדה ברורה בין שכבות
- Transactional Outbox Pattern מצוין לאמינות שליחת הודעות
- Circuit Breaker על כל קריאת API חיצונית
- מערכת בדיקות מקיפה עם בדיקות property-based ותרחישים end-to-end
- לוגים מובנים (JSON) עם correlation IDs ומיסוך מספרי טלפון
- ולידציית קלט מקיפה כולל הגנה מ-SQL Injection ו-XSS
- אימות JWT + OTP עם רוטציית refresh tokens

### תחומים הדורשים שיפור
- **שימוש ב-`Float` לסכומים כספיים** — סיכון לאובדן דיוק
- **חסרים אינדקסים** על עמודות foreign key קריטיות
- **פונקציות webhook ענקיות** (490-705 שורות) — חריגה מהסטנדרט
- **חוסר ב-`with_for_update()`** ב-`WalletService`
- **חוסר ולידציית מעברי state** ב-handlers של Courier/Dispatcher/StationOwner

---

## ממצאים קריטיים

### 1. `Float` לסכומים כספיים — סיכון לאובדן דיוק

**חומרה: קריטית**

כל העמודות הכספיות במערכת משתמשות ב-`Float` במקום `Numeric`/`DECIMAL`. זה עלול לגרום לשגיאות עיגול בחישובים כספיים.

**קבצים מושפעים:**
- `app/db/models/courier_wallet.py` — `balance`, `credit_limit`
- `app/db/models/wallet_ledger.py` — `amount`, `balance_after`
- `app/db/models/station_wallet.py` — `balance`, `commission_rate`
- `app/db/models/station_ledger.py` — `amount`, `balance_after`
- `app/db/models/manual_charge.py` — `amount`
- `app/db/models/delivery.py` — `fee`

**הצעת תיקון:**
```python
# לפני (שגוי)
balance = Column(Float, default=0.0)

# אחרי (נכון)
from sqlalchemy import Numeric
balance = Column(Numeric(10, 2), default=0.0)
```

**מיגרציה נדרשת:**
```sql
ALTER TABLE courier_wallets ALTER COLUMN balance TYPE NUMERIC(10,2);
ALTER TABLE courier_wallets ALTER COLUMN credit_limit TYPE NUMERIC(10,2);
ALTER TABLE wallet_ledger ALTER COLUMN amount TYPE NUMERIC(10,2);
ALTER TABLE wallet_ledger ALTER COLUMN balance_after TYPE NUMERIC(10,2);
ALTER TABLE station_wallets ALTER COLUMN balance TYPE NUMERIC(10,2);
ALTER TABLE station_ledger ALTER COLUMN amount TYPE NUMERIC(10,2);
ALTER TABLE station_ledger ALTER COLUMN balance_after TYPE NUMERIC(10,2);
ALTER TABLE manual_charges ALTER COLUMN amount TYPE NUMERIC(10,2);
ALTER TABLE deliveries ALTER COLUMN fee TYPE NUMERIC(10,2);
```

---

### 2. חסר `with_for_update()` ב-WalletService

**חומרה: קריטית**

ב-`app/domain/services/wallet_service.py`, הפונקציות `debit_for_capture()` ו-`credit_for_delivery()` קוראות ארנק ומעדכנות אותו **ללא נעילת שורה**. שתי בקשות מקביליות עלולות לגרום ל-double-debit או יתרה שגויה.

**השוואה:**
- `CaptureService.capture_delivery()` — **משתמש ב-`with_for_update()` כנדרש**
- `StationService.credit_station_commission()` — **משתמש ב-`for_update=True` כנדרש**
- `WalletService.debit_for_capture()` — **חסר!**
- `WalletService.credit_for_delivery()` — **חסר!**

**הצעת תיקון ב-`wallet_service.py`:**
```python
# debit_for_capture() — להוסיף with_for_update()
wallet_result = await self.db.execute(
    select(CourierWallet)
    .where(CourierWallet.courier_id == courier_id)
    .with_for_update()  # נעילת שורה למניעת race condition
)

# credit_for_delivery() — אותו דבר
wallet_result = await self.db.execute(
    select(CourierWallet)
    .where(CourierWallet.courier_id == courier_id)
    .with_for_update()
)
```

---

### 3. חסרים אינדקסים על foreign keys קריטיים

**חומרה: גבוהה**

מספר עמודות FK חשובות חסרות אינדקסים, מה שיגרום ל-full table scan בשאילתות תכופות.

| טבלה | עמודה | שימוש | אינדקס |
|-------|--------|--------|---------|
| `deliveries` | `sender_id` | שליפת משלוחים לפי שולח | **חסר** |
| `deliveries` | `courier_id` | שליפת משלוחים לפי שליח | **חסר** |
| `deliveries` | `requesting_courier_id` | זרימת אישור | **חסר** |
| `stations` | `owner_id` | שליפת תחנות לפי בעלים | **חסר** |
| `stations` | `is_active` | סינון תחנות פעילות | **חסר** |
| `outbox_messages` | `next_retry_at` | polling לניסיון חוזר | **חסר** |
| `outbox_messages` | `recipient_id` | הודעות לנמען | **חסר** |
| `users` | `role` | סינון לפי תפקיד | **חסר** |

**הצעת מיגרציה:**
```sql
CREATE INDEX idx_deliveries_sender_id ON deliveries(sender_id);
CREATE INDEX idx_deliveries_courier_id ON deliveries(courier_id);
CREATE INDEX idx_deliveries_requesting_courier_id ON deliveries(requesting_courier_id);
CREATE INDEX idx_stations_owner_id ON stations(owner_id);
CREATE INDEX idx_stations_is_active ON stations(id) WHERE is_active = TRUE;
CREATE INDEX idx_outbox_next_retry ON outbox_messages(next_retry_at)
    WHERE status IN ('pending', 'failed');
```

---

## שכבת מסד הנתונים

### מודלים — דברים טובים
- `BigInteger` ל-Telegram IDs (כנדרש — ID-ים חורגים מ-int32)
- `UniqueConstraint` על `(station_id, user_id)` ב-dispatchers ו-owners
- `UniqueConstraint` על `(courier_id, delivery_id, entry_type)` ב-`wallet_ledger` — מונע חיוב כפול
- Secure token ב-`Delivery` — מונע ניחוש ID-ים
- מודל `WebhookEvent` לאידמפוטנטיות
- Transactional Outbox Pattern עם ניהול retry

### מודלים — הצעות לשיפור

#### חסר UniqueConstraint ב-StationLedger
`station_ledger` לא מגן מפני עמלות כפולות, בניגוד ל-`wallet_ledger`:

```python
# wallet_ledger — מוגן (קיים)
__table_args__ = (
    UniqueConstraint('courier_id', 'delivery_id', 'entry_type',
                     name='uq_courier_delivery_type'),
)

# station_ledger — חסר הגנה!
# הצעה: להוסיף
__table_args__ = (
    UniqueConstraint('station_id', 'delivery_id', 'entry_type',
                     name='uq_station_delivery_type'),
)
```

#### חסר UniqueConstraint ב-ConversationSession
אין מניעה ליצירת כמה sessions לאותו משתמש ופלטפורמה:

```python
# הצעה
__table_args__ = (
    UniqueConstraint('user_id', 'platform',
                     name='uq_conversation_user_platform'),
)
```

### Relationships — Lazy Loading

כל ה-relationships מוגדרים כ-lazy (ברירת מחדל). זה מסוכן כי גישה ל-`delivery.sender.name` מייצרת query נוסף.

**המלצה:** להוסיף helper functions עם eager loading לשאילתות נפוצות:

```python
# בשכבת ה-service
async def get_delivery_with_users(db: AsyncSession, delivery_id: int) -> Delivery:
    result = await db.execute(
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .options(
            joinedload(Delivery.sender),
            joinedload(Delivery.courier),
        )
    )
    return result.scalar_one_or_none()
```

---

## שכבת ה-Domain ושירותים

### דברים טובים
- **CaptureService** — אטומיות מצוינת עם `with_for_update()` על ארנק ומשלוח
- **ShipmentWorkflowService** — שרשרת אטומית: request → approve → capture → wallet
- **StationService** — savepoint + IntegrityError fallback לתנאי race
- **כל קריאות API חיצוני עטופות ב-Circuit Breaker**
- **ולידציית סטטוס** לפני כל מעבר סטטוס במשלוח
- **הרשאות** — בדיקות authorization מקיפות בכל פעולה
- **Phone masking** — כל מספרי הטלפון מוסתרים בלוגים
- **אין `print()`** — כל הקוד משתמש ב-logger

### הצעות לשיפור

#### אופטימיזציה של שאילתות ב-ShipmentWorkflowService
ב-`approve_delivery()` נעשות שתי שאילתות נפרדות למשתמשים:

```python
# מצב נוכחי — שתי שאילתות
courier = await self._get_user(courier_id)
dispatcher = await self._get_user(dispatcher_id)

# הצעה — שאילתה אחת
users_result = await self.db.execute(
    select(User).where(User.id.in_([courier_id, dispatcher_id]))
)
users = {u.id: u for u in users_result.scalars().all()}
courier = users.get(courier_id)
dispatcher = users.get(dispatcher_id)
```

#### חוסר בלוגים ב-tasks.py
ב-`tasks.py` שורות 432-436, שגיאות ב-gather results לא מתועדות:

```python
# מצב נוכחי
for r in results:
    if isinstance(r, Exception):
        final_results.append({"success": False, "error": str(r)})

# הצעה — להוסיף לוג
for r in results:
    if isinstance(r, Exception):
        logger.error("כשלון בעיבוד הודעה", extra_data={"error": str(r)}, exc_info=True)
        final_results.append({"success": False, "error": str(r)})
```

#### Type hints — שיפור מינורי
- `WalletService.get_ledger_history()` — מחזיר `list` במקום `list[WalletLedger]`
- כמה פונקציות ב-tasks.py חסרות type hints מדויקים

---

## שכבת ה-API ו-Webhooks

### דברים טובים
- תיעוד OpenAPI מקיף עם סיכומים בעברית
- Idempotency mechanism ב-WhatsApp (דרך טבלת `webhook_events`)
- Retry logic עם exponential backoff בשליחת הודעות WhatsApp
- Security headers (HSTS, CSP, X-Content-Type-Options)
- Correlation IDs בכל בקשה
- Admin API key protection על endpoints רגישים
- JWT + OTP authentication לפאנל

### ממצאים

#### פונקציות webhook ענקיות — הפרה של סטנדרט

**חומרה: גבוהה**

לפי `CLAUDE.md`, handler צריך להיות ~30 שורות. במצב הנוכחי:

| פונקציה | שורות | קובץ |
|----------|--------|------|
| `telegram_webhook()` | ~490 | `app/api/webhooks/telegram.py` |
| `whatsapp_webhook()` | ~705 | `app/api/webhooks/whatsapp.py` |

**המלצה:** לפצל לפונקציות עם אחריות ברורה:

```python
# במקום פונקציה ענקית אחת:
async def telegram_webhook(update: dict, ...):
    # 490 שורות...

# לפצל ל:
async def telegram_webhook(update: dict, ...):
    """נקודת כניסה — ניתוב בלבד"""
    if update.get("callback_query"):
        return await _handle_callback_query(update, db, ...)
    if update.get("message"):
        return await _handle_message(update, db, ...)

async def _handle_callback_query(update, db, ...):
    """טיפול ב-callback queries"""
    ...

async def _handle_message(update, db, ...):
    """טיפול בהודעות טקסט ומדיה"""
    ...
```

#### `else` גנרי בניתוב לפי תפקיד — הפרה של כלל 8

**חומרה: גבוהה**

בשני ה-webhooks, ניתוב לתפקיד משתמש `_route_to_role_menu()` מסתיים ב-fallback גנרי:

```python
# telegram.py ~שורה 692 ו-whatsapp.py ~שורה 865
if user.role == UserRole.COURIER:
    ...
if user.role == UserRole.STATION_OWNER:
    ...
if user.role == UserRole.SENDER or user.role == UserRole.ADMIN:
    ...
# fallback — גנרי, תפקידים לא צפויים נופלים לכאן
logger.warning("Unknown user role...")
return await _sender_fallback(...)
```

**תיקון מומלץ:**
```python
if user.role == UserRole.COURIER:
    ...
elif user.role == UserRole.STATION_OWNER:
    ...
elif user.role == UserRole.SENDER:
    ...
elif user.role == UserRole.ADMIN:
    ...
else:
    logger.error("תפקיד לא מוכר", extra_data={"role": str(user.role)})
    raise ValueError(f"Unknown role: {user.role}")
```

#### חסרה אימות חתימת Webhook

**חומרה: בינונית**

- Telegram webhooks לא מאמתים את ה-`X-Telegram-Bot-Api-Secret-Token` header
- WhatsApp — ה-verify endpoint קיים אבל ה-POST לא מוודא חתימה

**המלצה:** להוסיף אימות חתימה ב-middleware:
```python
# Telegram — להגדיר secret_token ב-setWebhook ולוודא בכל בקשה
secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
if secret != settings.TELEGRAM_WEBHOOK_SECRET:
    raise HTTPException(status_code=403, detail="Invalid signature")
```

#### Endpoint ציבורי לחיפוש משתמש לפי טלפון

**חומרה: בינונית**

`app/api/routes/users.py` — `get_user_by_phone` מאפשר enumeration של משתמשים לפי מספר טלפון ללא הגבלה.

**המלצה:** להוסיף admin API key requirement או rate limiting.

#### חסר Rate Limiting על webhooks

**חומרה: בינונית**

שני ה-webhook endpoints חשופים ללא rate limiting. תוקף יכול להציף את המערכת.

**המלצה:** להוסיף rate limiting middleware:
```python
from fastapi import Request
from datetime import datetime, timedelta

# דוגמה בסיסית — sliding window
RATE_LIMIT = 100  # בקשות
WINDOW = 60  # שניות

async def rate_limit_middleware(request: Request):
    key = f"rate:{request.client.host}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, WINDOW)
    if count > RATE_LIMIT:
        raise HTTPException(status_code=429)
```

---

## מכונת מצבים (State Machine)

### דברים טובים
- Enum-ים ברורים עם prefixes לכל תפקיד
- StateManager מרכזי עם ולידציית מעברים
- XSS prevention דרך `html.escape()` על קלט
- Race condition check ב-`_handle_terms()` לרישום שליח
- ניקוי context ב-SenderHandler בחזרה לתפריט

### ממצאים

#### חסרה ולידציית מעברים ב-3 מתוך 4 handlers

**חומרה: גבוהה**

`SenderStateHandler` משתמש ב-`transition_to()` (עם ולידציה), אבל שלושת האחרים משתמשים **רק** ב-`force_state()` שעוקף את כל הולידציה:

| Handler | שימוש ב-transition_to() | שימוש ב-force_state() |
|---------|--------------------------|------------------------|
| SenderStateHandler | כן (עם fallback ל-force) | כ-fallback |
| CourierStateHandler | **לא** | **תמיד** |
| DispatcherStateHandler | **לא** | **תמיד** |
| StationOwnerStateHandler | **לא** | **תמיד** |

**המלצה:** להשתמש ב-`transition_to()` ראשית בכל ה-handlers, עם `force_state()` רק ל-admin/reset.

#### חסרים handlers לסטייטים של שליח

**חומרה: בינונית**

`CourierStateHandler` חסר handlers עבור:
- `CourierState.VIEW_AVAILABLE`
- `CourierState.CAPTURE_CONFIRM`
- `CourierState.MARK_PICKED_UP`
- `CourierState.MARK_DELIVERED`

כל הסטייטים הנ"ל נופלים ל-`_handle_unknown()` שלא מממש פעולות משלוח.

**הערה:** ייתכן שזרימות אלו מטופלות ישירות ב-webhook handler — אם כן, מומלץ לתעד זאת.

#### חסר Guard על multi-step flows

**חומרה: בינונית**

ב-`CLAUDE.md` מוגדר שאסור לבדוק `"keyword" in text` ללא guard על state. ב-Dispatcher ו-StationOwner handlers אין guard check:

```python
# סיכון: משתמש באמצע הזנת כתובת כותב "תפריט"
# וה-handler תופס את זה כפקודת ניתוב

# הצעה: להוסיף guard בתחילת כל handler
def _should_skip_keyword_routing(self, current_state: str) -> bool:
    multi_step_prefixes = [
        "DISPATCHER.ADD_SHIPMENT_",
        "STATION.ADD_BLACKLIST_",
        "STATION.REMOVE_",
    ]
    return any(current_state.startswith(p) for p in multi_step_prefixes)
```

#### חסר ניקוי context ב-CourierHandler

**חומרה: נמוכה**

`SenderStateHandler` מנקה context של משלוח בחזרה לתפריט, אבל `CourierStateHandler` לא. מסמכי KYC (תעודת זהות, סלפי, צילום רכב) נשארים ב-context.

**המלצה:** להוסיף ניקוי context דומה בחזרה לתפריט שליח.

---

## תשתית Core

### דברים טובים
- **Logging**: JSON formatting עם correlation IDs, operation decorators
- **Validation**: מקיפה — טלפון, כתובת, שם, סכום, הגנה מ-injection
- **Exceptions**: היררכיה ברורה עם error codes (1xxx-6xxx)
- **Circuit Breaker**: thread-safe, singleton pattern, 3 breakers מוגדרים מראש
- **Auth**: OTP + JWT + refresh token rotation, atomic Redis operations
- **Middleware**: correlation ID, request logging, security headers

### הצעות לשיפור

#### JWT_SECRET_KEY — אזהרה בלבד ולא אכיפה

**חומרה: בינונית**

ב-`app/core/config.py`, אם `JWT_SECRET_KEY` ריק, המערכת רק מוציאה `warnings.warn()` אבל ממשיכה לעבוד. בפרודקשן זה מסוכן.

**המלצה:**
```python
@field_validator("JWT_SECRET_KEY")
@classmethod
def validate_jwt_secret(cls, v: str) -> str:
    if not v and os.getenv("ENV", "dev") == "production":
        raise ValueError("JWT_SECRET_KEY חייב להיות מוגדר בפרודקשן")
    return v
```

#### Security Headers רק בפרודקשן

HSTS headers מופעלים רק כש-`DEBUG=False`. זה הגיוני, אבל צריך לוודא שבפרודקשן `DEBUG=False` תמיד.

#### OTP — plaintext ב-Redis

OTP נשמר כ-plaintext ב-Redis (מוגבל ל-5 דקות TTL). עדיף לשמור hash:

```python
import hashlib

# שמירה
otp_hash = hashlib.sha256(otp.encode()).hexdigest()
await redis.set(f"otp:{user_id}", otp_hash, ex=300)

# אימות
submitted_hash = hashlib.sha256(submitted_otp.encode()).hexdigest()
stored_hash = await redis.get(f"otp:{user_id}")
if submitted_hash == stored_hash:
    ...
```

---

## בדיקות

### סיכום מצב

| תחום | כיסוי | הערות |
|------|--------|-------|
| Webhook handlers + State Machine | מצוין | ~4,300 שורות בדיקות |
| ולידציה וסניטציה | מצוין | כולל property-based testing |
| פעולות כספיות | מצוין | כולל concurrency tests |
| אימות וזיהוי (Auth) | מצוין | OTP, JWT, refresh tokens |
| API routes | טוב | רוב ה-endpoints מכוסים |
| Panel routes | טוב | רוב הפאנל מכוסה |
| Celery Workers | **חסר** | אין בדיקות ישירות ל-tasks |
| Middleware | **חסר** | אין בדיקות ייעודיות |
| מיגרציות | **חסר** | אין בדיקות rollback |
| Panel groups/owners | **חסר** | אין קבצי בדיקה ייעודיים |

### נקודות חזקות
- **~962 פונקציות בדיקה** ב-45+ קבצים
- **7 תרחישי end-to-end** (full delivery, cross-platform, concurrent capture, ועוד)
- **Property-based testing** עם Hypothesis — מצוין לבדיקת invariants
- **Factory fixtures** נקיים ומסודרים (`user_factory`, `delivery_factory`)
- **Mock patterns** מצוינים — FakeRedis, AsyncMock לשירותים חיצוניים
- **בדיקות אבטחה** — SQL injection, XSS, user enumeration prevention

### המלצות

#### הוספת בדיקות ל-Celery Workers — עדיפות גבוהה

`app/workers/tasks.py` (21K) אחראי על שליחת כל ההודעות, אבל אין לו בדיקות ישירות.

**מה לבדוק:**
- עיבוד הודעות outbox — happy path
- retry logic עם exponential backoff
- כשלון שליחה ו-dead letter handling
- broadcast filtering (סינון נמענים)
- מעבר בין פלטפורמות (Telegram ↔ WhatsApp)

#### הוספת בדיקות ביצועים

**מה לבדוק:**
- ספירת queries בפעולות נפוצות (מניעת N+1)
- benchmark לפעולות batch
- זיכרון ב-broadcasts גדולים

#### הוספת בדיקות Middleware

**מה לבדוק:**
- Correlation ID propagation
- Security headers בפרודקשן
- Exception handler behavior
- Request/response logging

---

## אבטחה

### מצב טוב

| תחום | מצב |
|------|------|
| הגנה מ-SQL Injection | מיושם (regex + TextSanitizer) |
| הגנה מ-XSS | מיושם (html.escape + TextSanitizer) |
| מיסוך טלפונים בלוגים | מיושם (PhoneNumberValidator.mask) |
| JWT + Refresh Token Rotation | מיושם |
| Circuit Breaker | מיושם על כל API חיצוני |
| Idempotency | מיושם (webhook_events) |
| Admin API Key | מיושם |
| Security Headers | מיושם (HSTS, CSP, nosniff) |

### נקודות לשיפור

| נושא | חומרה | המלצה |
|------|--------|--------|
| חסרה אימות חתימת webhook | בינונית | להוסיף `X-Telegram-Bot-Api-Secret-Token` verification |
| אין rate limiting על webhooks | בינונית | להוסיף sliding window rate limiter |
| endpoint ציבורי לחיפוש טלפון | בינונית | להוסיף admin key או rate limiting |
| OTP plaintext ב-Redis | נמוכה | לשמור hash במקום plaintext |
| JWT secret לא נאכף | בינונית | לזרוק exception בפרודקשן |

---

## ביצועים

### N+1 Queries — מצב

המערכת **בדרך כלל** מקפידה על שאילתות יעילות, אבל יש כמה מקומות לשיפור:

| מיקום | בעיה | פתרון |
|-------|-------|--------|
| `Delivery.sender` (lazy) | כל גישה = query נוסף | `joinedload` בשאילתות |
| `Delivery.courier` (lazy) | כל גישה = query נוסף | `joinedload` בשאילתות |
| `ShipmentWorkflowService.approve_delivery()` | 2 queries נפרדות למשתמשים | `WHERE id IN (...)` |
| חוסר אינדקסים על FK | full table scan | הוספת אינדקסים |

### Eager Loading — המלצה גלובלית

ליצור פונקציות helper לשאילתות נפוצות:

```python
# app/db/queries.py (חדש)
from sqlalchemy.orm import joinedload, selectinload

def delivery_with_relations():
    """options נפוצים לשליפת משלוח עם משתמשים"""
    return [
        joinedload(Delivery.sender),
        joinedload(Delivery.courier),
        joinedload(Delivery.requesting_courier),
    ]

# שימוש
result = await db.execute(
    select(Delivery)
    .where(Delivery.id == delivery_id)
    .options(*delivery_with_relations())
)
```

---

## סיכום הצעות לשיפור

### עדיפות קריטית (לטפל מיד)

| # | נושא | קובץ | שורות |
|---|-------|------|--------|
| 1 | החלפת `Float` ל-`Numeric(10,2)` בכל עמודות כספיות | 6 קבצי מודלים | מיגרציה 009 |
| 2 | הוספת `with_for_update()` ב-`WalletService` | `wallet_service.py` | ~71, ~103 |
| 3 | הוספת אינדקסים על FK חסרים | מיגרציה חדשה | 6 אינדקסים |

### עדיפות גבוהה

| # | נושא | קובץ |
|---|-------|------|
| 4 | פיצול פונקציות webhook ענקיות | `telegram.py`, `whatsapp.py` |
| 5 | תיקון `else` גנרי בניתוב תפקידים | `telegram.py`, `whatsapp.py` |
| 6 | הוספת `transition_to()` ב-Courier/Dispatcher/StationOwner handlers | 3 קבצי handlers |
| 7 | הוספת `UniqueConstraint` ל-`station_ledger` | `station_ledger.py` |
| 8 | הוספת `UniqueConstraint` ל-`conversation_sessions` | `conversation_session.py` |

### עדיפות בינונית

| # | נושא |
|---|-------|
| 9 | הוספת אימות חתימת webhook (Telegram + WhatsApp) |
| 10 | הוספת rate limiting על webhook endpoints |
| 11 | הגנה על endpoint חיפוש טלפון |
| 12 | אכיפת JWT_SECRET_KEY בפרודקשן |
| 13 | הוספת guard checks ל-multi-step flows |
| 14 | ניקוי context ב-CourierHandler |
| 15 | הוספת בדיקות ל-Celery Workers |

### עדיפות נמוכה

| # | נושא |
|---|-------|
| 16 | אופטימיזציה של שאילתות משתמשים ב-ShipmentWorkflowService |
| 17 | שיפור type hints ב-WalletService ו-tasks.py |
| 18 | הוספת בדיקות Middleware |
| 19 | הוספת בדיקות מיגרציות |
| 20 | Hashing של OTP ב-Redis |

---

## ציון כללי

| קטגוריה | ציון (1-10) | הערות |
|----------|-------------|-------|
| ארכיטקטורה | **9** | מרובדת ומסודרת, patterns מתקדמים |
| אבטחה | **7.5** | טוב ברובו, חסרים webhook signatures ו-rate limiting |
| ביצועים | **7** | חסרים אינדקסים, Float לכסף, lazy loading |
| בדיקות | **8.5** | מקיפות מאוד, חסר כיסוי ל-workers |
| קוד quality | **7.5** | handlers ענקיים, חסר ולידציית state transitions |
| תיעוד | **9** | מקיף ומפורט, עברית אחידה |
| **ממוצע** | **8.1** | פרויקט בשל עם תחומי שיפור ברורים |
