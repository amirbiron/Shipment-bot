# קוד ריוויו מקיף - Shipment Bot

> תאריך: 2026-03-05
> סריקה אוטומטית של כל שכבות המערכת: תשתית ליבה, מכונת מצבים, שירותים עסקיים, webhooks, בדיקות ותשתית

---

## תוכן עניינים

1. [סיכום מנהלים](#סיכום-מנהלים)
2. [ממצאים קריטיים](#ממצאים-קריטיים)
3. [אבטחה ופרטיות](#אבטחה-ופרטיות)
4. [ביצועים ויציבות](#ביצועים-ויציבות)
5. [מכונת מצבים](#מכונת-מצבים)
6. [שירותים עסקיים](#שירותים-עסקיים)
7. [תשתית ליבה](#תשתית-ליבה)
8. [בדיקות וכיסוי קוד](#בדיקות-וכיסוי-קוד)
9. [הצעות לפיצ'רים חדשים](#הצעות-לפיצרים-חדשים)
10. [תוכנית פעולה מתועדפת](#תוכנית-פעולה-מתועדפת)

---

## סיכום מנהלים

המערכת בנויה בצורה מוצקה עם ארכיטקטורה ברורה, אך נמצאו **12 ממצאים קריטיים** שדורשים טיפול מיידי, **23 ממצאים בינוניים** לטיפול בטווח הקצר, ו-**15 שיפורים** מומלצים לטווח הארוך.

### טבלת ציון לפי תחום

| תחום | ציון | הערות |
|------|------|-------|
| Webhooks + ניתוב | 9/10 | כל התפקידים מטופלים, guard functions תקינים, OpenAPI מתועד |
| מסיכת טלפונים בלוגים | 9/10 | שימוש עקבי ב-`PhoneNumberValidator.mask()` |
| מכונת מצבים | 7/10 | חסרים handlers, בדיקות רישום קיים חסרות בשולח/שליח |
| שירותים עסקיים | 6/10 | חסרות בדיקות authorization ו-validate_against_existing |
| פעולות ארנק | 5/10 | חסר `with_for_update()` בחלק מהפעולות |
| בדיקות | 6/10 | כיסוי טוב ל-webhooks, פערים גדולים בשירותים ו-API routes |
| תשתית | 7/10 | circuit breaker ו-middleware טובים, בעיית ביצועים ב-DB |

---

## ממצאים קריטיים

### 1. יצירת Engine חדש בכל Task של Celery

**קובץ:** `app/db/database.py` שורות 43-62
**חומרה:** קריטית

`get_task_session()` יוצר `create_async_engine()` חדש **בכל קריאה**. כל engine מקים connection pool חדש — זה גורם ל:
- דליפת זיכרון (engines ישנים לא מתפנים מיד)
- מיצוי חיבורים ל-DB
- איטיות בביצוע tasks

```python
# מצב נוכחי - בעייתי
async def get_task_session():
    task_engine = create_async_engine(...)  # engine חדש בכל קריאה!
    ...
    await task_engine.dispose()

# מומלץ - singleton per worker
_task_engine = None

async def get_task_engine():
    global _task_engine
    if _task_engine is None:
        _task_engine = create_async_engine(...)
    return _task_engine
```

---

### 2. חסרה בדיקת רישום קיים ב-`_handle_initial()` של שולח ושליח

**קובץ:** `app/state_machine/handlers.py` שורות 150-155 (שולח), 854-864 (שליח)
**חומרה:** קריטית
**הפרת כלל:** CLAUDE.md כלל #18

כשמשתמש רשום מקבל reset ל-INITIAL (דרך `/start` או `_route_to_role_menu`), ה-handler מתחיל רישום מחדש **בלי לבדוק אם כבר רשום**. זה עלול לדרוס נתונים קיימים.

```python
# מצב נוכחי - חסרה בדיקה
async def _handle_initial(self, user, message, context):
    return welcome_response, State.REGISTER_STEP_1.value, {}

# מומלץ - כמו שכבר מיושם ב-DriverStateHandler שורות 291-295
async def _handle_initial(self, user, message, context):
    profile = await self._get_profile(user.id)
    if profile and profile.is_registration_complete:
        return response, State.MENU.value, {}
    # ... המשך רישום רגיל
```

---

### 3. פעולות ארנק ללא `with_for_update()` — מירוץ תהליכים

**קובץ:** `app/domain/services/wallet_service.py` שורות 65-85
**חומרה:** קריטית
**הפרת כלל:** CLAUDE.md כלל #10

`get_balance()` ו-`check_can_capture()` קוראים יתרה **בלי נעילת שורה**. בתנאי מירוץ (שני שליחים תופסים משלוח במקביל), שניהם יכולים לעבור את הבדיקה ולגרום ליתרה שלילית.

```python
# מומלץ - נעילת שורה לפני קריאה קריטית
async def check_can_capture(self, courier_id: int, amount: Decimal) -> bool:
    wallet = await session.execute(
        select(CourierWallet)
        .where(CourierWallet.courier_id == courier_id)
        .with_for_update()  # נעילת שורה
    )
```

---

### 4. Handler חסר ל-`REGISTER_COLLECT_PHONE` בשולח

**קובץ:** `app/state_machine/states.py` שורה 101 (מעבר מוגדר), `app/state_machine/handlers.py` (handler חסר)
**חומרה:** קריטית

המעבר `REGISTER_COLLECT_NAME → REGISTER_COLLECT_PHONE` מוגדר ב-TRANSITIONS, אבל **אין handler מתאים**. אם ה-state machine מגיע ל-state הזה, הוא נופל ל-`_handle_unknown()`.

---

### 5. חסרות קריאות ל-`validate_against_existing()` בשירותי נהג

**קובץ:** `app/domain/services/driver_menu_service.py` שורות 212-284
**חומרה:** קריטית
**הפרת כלל:** CLAUDE.md כלל #16

3 מתוך 5 מתודות עדכון (`update_vehicle_type`, `update_trip_type`, `update_show_deliveries`) יוצרות `DriverSearchSettingsUpdate` **בלי לקרוא ל-`validate_against_existing()`**. ולידציה צולבת לא תתפוס שילובים לא תקינים.

רק `update_timeframe` (שורות 313-317) ו-`update_future_only` (שורות 362-366) מיישמים נכון.

---

### 6. חסרות בדיקות authorization בשירותי נהג

**קבצים:** `app/domain/services/driver_search_service.py`, `app/domain/services/driver_menu_service.py`
**חומרה:** קריטית
**הפרת כלל:** CLAUDE.md כלל #12

- `resume_all_searches()` (שורות 256-342) — **ללא בדיקת בעלות**
- `pause_all_searches()` (שורות 225-254) — **ללא בדיקת בעלות**
- כל מתודות ה-update ב-`driver_menu_service` — מניחות שהקורא מורשה

---

## אבטחה ופרטיות

### 7. Correlation ID קצר מדי — סיכון להתנגשויות

**קובץ:** `app/core/logging.py` שורות 143-145
**חומרה:** בינונית

Correlation ID נוצר מ-8 תווים ראשונים של UUID — רק ~32 סיביות אנטרופיה. בנפח גבוה, סיכוי גבוה להתנגשויות.

```python
# מצב נוכחי
return str(uuid.uuid4())[:8]  # 32 סיביות

# מומלץ - 16 תווים לפחות
return uuid.uuid4().hex[:16]  # 64 סיביות
```

---

### 8. חשיפת נתונים פיננסיים בשגיאות

**קובץ:** `app/core/exceptions.py` שורות 251-256
**חומרה:** בינונית

`InsufficientCreditError` מחזיר `current_balance` ו-`available_credit` ב-details — חושף מידע פיננסי ללקוח.

```python
# מצב נוכחי - חושף יתרה מדויקת
details={"current_balance": current_balance, "available_credit": ...}

# מומלץ - הודעה כללית בלבד
details={"message": "אין מספיק יתרה לביצוע הפעולה"}
```

---

### 9. חסרים דפוסי SQL Injection בולידציה

**קובץ:** `app/core/validation.py` שורות 43-61
**חומרה:** בינונית

חסר דפוס זיהוי ל-`LIKE '%...'` — וקטור תקיפה נפוץ. מומלץ להוסיף:

```python
re.compile(r"\bLIKE\s+['\"%]", re.IGNORECASE)
```

---

### 10. HSTS max-age אגרסיבי מדי

**קובץ:** `app/core/middleware.py` שורה 193
**חומרה:** בינונית

HSTS מוגדר לשנה (31,536,000 שניות). אם תעודת SSL תפקע, לקוחות יסרבו ל-HTTP למשך שנה. מומלץ להתחיל ב-604,800 (שבוע).

---

## ביצועים ויציבות

### 11. בעיות N+1 Queries

**קבצים:**
- `app/domain/services/delivery_service.py` שורות 114-130 — שליפת משלוחים ללא eager loading
- `app/domain/services/driver_menu_service.py` שורות 90-157 — שליפות נפרדות ל-profile, settings, user

```python
# מומלץ
query = select(Delivery).options(
    joinedload(Delivery.sender),
    selectinload(Delivery.status_logs)
)
```

---

### 12. אינדקסים כפולים על עמודות UNIQUE

**קבצים:**
- `app/db/models/user.py` שורה 36: `phone_number = Column(..., unique=True, index=True)` — כפול
- `app/db/models/delivery.py` שורה 45: `token = Column(..., unique=True, ..., index=True)` — כפול

**הפרת כלל:** CLAUDE.md כלל #15 — PostgreSQL יוצר אינדקס אוטומטית ל-UNIQUE.

---

### 13. דליפת זיכרון ב-Rate Limiter

**קובץ:** `app/core/middleware.py` שורות 237-239
**חומרה:** בינונית

ניקוי rate limit מוחק IP רק כשרשימת הבקשות שלו ריקה. IP ששולח בקשה אחת בכל חלון זמן **לעולם לא יימחק** — גורם לגדילת זיכרון לא מוגבלת.

```python
# מומלץ - ניקוי גם לפי זמן אחרון
if not self._requests[ip] or (now - self._requests[ip][-1]) > window * 10:
    del self._requests[ip]
```

---

### 14. שימוש ב-`datetime.utcnow()` (Deprecated)

**קובץ:** `app/core/logging.py` שורות 24, 176, 185, 197, 221, 230, 242
**חומרה:** נמוכה

`datetime.utcnow()` deprecated מ-Python 3.12. מומלץ:

```python
datetime.now(timezone.utc).isoformat()
```

---

### 15. חסר Connection Timeout ל-DB

**קובץ:** `app/db/database.py` שורות 10-14
**חומרה:** בינונית

אין timeout מוגדר לחיבורי DB — חיבור איטי יתקע ללא הגבלה.

```python
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=20,
    connect_args={"timeout": 10}
)
```

---

## מכונת מצבים

### 16. Guard function חלקי ב-Dispatcher

**קובץ:** `app/state_machine/dispatcher_handler.py` שורות 91-97
**חומרה:** בינונית
**הפרת כלל:** CLAUDE.md כלל #19

`_is_multi_step_flow_state()` מכסה רק `ADD_SHIPMENT`, `MANUAL_CHARGE`, `POST_RIDE`. אם יתווסף flow חדש (כמו `ISSUE_REFUND`), ה-guard לא יכסה אותו.

---

### 17. חסר state `ADD_SHIPMENT_DROPOFF_APARTMENT` לסדרן

**קובץ:** `app/state_machine/states.py` שורות 241-249
**חומרה:** בינונית

הסדרן לא יכול לציין דירה/יחידה בכתובת יעד, בניגוד לשולח. חסרה עקביות בין הזרימות.

---

### 18. StateManager — shallow copy של context

**קובץ:** `app/state_machine/manager.py` שורות 95-99
**חומרה:** נמוכה

עדכון context יוצר עותק רדוד (`dict(...)`) — אובייקטים מקוננים לא מעתיקים, וייתכן ש-SQLAlchemy לא יזהה שינויים בהם.

---

## שירותים עסקיים

### 19. חריגות שנבלעות ללא לוגים

**קובץ:** `app/domain/services/admin_notification_service.py`
**שורות:** 961-962, 1018-1020, 1042-1044
**הפרת כלל:** CLAUDE.md כלל #11

```python
# מצב נוכחי - בולע שגיאות
except Exception:
    pass

# מומלץ
except Exception as e:
    logger.error("כשלון בעיבוד", extra_data={"error": str(e)}, exc_info=True)
```

---

### 20. Authorization אופציונלי ב-Station Service

**קובץ:** `app/domain/services/station_service.py` שורות 61-74
**חומרה:** בינונית

`_verify_station_owner()` מחזיר `True` כש-`actor_user_id=None`. שירות פנימי שקורא בלי actor_user_id עוקף authorization — מסוכן אם נקרא בטעות ממקום לא מוגן.

---

### 21. ולידציית AmountValidator שברירית

**קובץ:** `app/core/validation.py` שורות 520-521
**חומרה:** בינונית

שימוש ב-floating point לבדיקת 2 ספרות אחרי הנקודה — עלול לתת תוצאות שגויות. מומלץ `Decimal`.

---

## תשתית ליבה

### 22. חסר ולידציה לערכי Rate Limit

**קובץ:** `app/core/config.py` שורות 122-124
**חומרה:** בינונית

אין ולידציה ש-`WEBHOOK_RATE_LIMIT_MAX_REQUESTS > 0` ו-`WEBHOOK_RATE_LIMIT_WINDOW_SECONDS > 0`. ערכים לא תקינים משביתים rate limiting בשקט.

---

### 23. Circuit Breaker — race condition ב-get_instance()

**קובץ:** `app/core/circuit_breaker.py` שורות 76-88
**חומרה:** בינונית

Double-checked locking — הבדיקה הראשונה (שורה 83) ללא lock עלולה להוביל ל-race condition. ה-GIL ב-Python מגן במקרים רבים, אבל הפתרון שביר.

---

### 24. Circuit Breaker Decorator יוצר event loop חדש

**קובץ:** `app/core/circuit_breaker.py` שורות 273-280
**חומרה:** בינונית

`sync_wrapper()` יוצר `asyncio.new_event_loop()` — ייכשל אם כבר רץ event loop (למשל מתוך FastAPI handler).

---

### 25. Fallback חסר לתפקידים לא מוכרים ב-WhatsApp Cloud

**קובץ:** `app/api/webhooks/whatsapp_cloud.py` שורות 545-750
**חומרה:** בינונית
**הפרת כלל:** CLAUDE.md כלל #8

ניתוב לפי תפקיד חסר `else` עם לוג ברור לתפקידים לא מזוהים.

---

## בדיקות וכיסוי קוד

### מצב נוכחי

| מדד | ערך |
|------|------|
| סך קבצי בדיקות | 77 |
| סך פונקציות בדיקה | 1,578 |
| בדיקות יחידה (מסומנות) | 613 |
| בדיקות אינטגרציה (מסומנות) | 20 |
| בדיקות תרחיש (E2E) | 7 |

### פערים קריטיים בכיסוי

#### שירותים עסקיים לא נבדקים (12 מתוך 21)

| שירות | סיכון |
|--------|--------|
| `capture_service` | קריטי — לוגיקת תפיסת משלוח |
| `delivery_service` | קריטי — CRUD משלוחים |
| `courier_approval_service` | קריטי — אישור KYC |
| `pricing_service` | גבוה — חישובי מחירים |
| `station_service` | גבוה — ניהול תחנות |
| `shipment_workflow_service` | גבוה — אורקסטרציית משלוחים |
| `driver_search_service` | בינוני — חיפוש נסיעות |
| `driver_menu_service` | בינוני — תפריט נהג |
| `driver_registration_service` | בינוני — רישום נהג |
| `driver_verification_service` | בינוני — אימות נהג |
| `city_abbreviation_service` | נמוך — קיצורי ערים |
| `ride_posting_service` | בינוני — פרסום נסיעות |

#### API Routes לא נבדקים

| נתיב | סיכון |
|------|--------|
| `routes/stations.py` | גבוה — יצירה/ניהול תחנות |
| `routes/wallets.py` | קריטי — פעולות פיננסיות |
| `routes/migrations.py` | גבוה — שינויי סכמה |
| `routes/panel/groups.py` | בינוני — ניהול קבוצות |
| `routes/panel/owners.py` | בינוני — ניהול בעלים |
| `routes/panel/settings.py` | בינוני — הגדרות מערכת |

### בעיות תשתית בדיקות

1. **אין סף כיסוי קוד** — `pytest-cov` מותקן אבל אין `.coveragerc` ואין minimum coverage
2. **SQLite במקום PostgreSQL** — בדיקות רצות על SQLite בזיכרון, לא תופסות פיצ'רים ספציפיים ל-PostgreSQL (JSON, partial indexes, check constraints)
3. **אין בדיקות מיגרציות** — 11 קבצי SQL ללא בדיקה, כולל קונפליקט שמות (שני קבצים עם prefix `001_`)
4. **CI/CD חלקי** — אין דיווח כיסוי, אין mypy, אין bandit, אין matrix testing
5. **timeout של CI קצר** — 10 דקות ל-1,578 בדיקות עלול להיות בלתי מספיק

### המלצות CI/CD

```yaml
# הוספה ל-.github/workflows/tests.yml
- name: Type checking
  run: mypy app/ --ignore-missing-imports

- name: Security scan
  run: bandit -r app/ -ll

- name: Coverage with threshold
  run: pytest --cov=app --cov-fail-under=80

timeout-minutes: 15  # העלאה מ-10
```

---

## הצעות לפיצ'רים חדשים

### פיצ'ר 1: מערכת ניטור בריאות (Health Dashboard)

**תיאור:** דשבורד שמציג את מצב כל הרכיבים בזמן אמת — DB, Redis, Celery workers, circuit breakers, שיעורי הצלחה של API חיצוניים.

**ערך עסקי:** זיהוי מוקדם של תקלות, הפחתת MTTR (Mean Time To Recovery).

**יישום מוצע:**
- endpoint `/health/detailed` שמחזיר מצב כל רכיב
- Celery task תקופתי שבודק ושולח התראה בחריגה
- שילוב עם dashboard קיים

---

### פיצ'ר 2: ביטול אוטומטי של משלוחים לא נתפסים

**תיאור:** משלוחים שלא נתפסו תוך X שעות — ביטול אוטומטי עם התראה לשולח.

**ערך עסקי:** מניעת "משלוחים מתים" שתוקעים את המערכת ומבלבלים שליחים.

**יישום מוצע:**
- שדה `expires_at` בטבלת `deliveries`
- Celery beat task כל 15 דקות לבדיקת פקיעה
- התראה לשולח 30 דקות לפני ביטול

---

### פיצ'ר 3: מנגנון Retry חכם להודעות

**תיאור:** כיום הודעות שנכשלות עוברות retry פשוט. מומלץ retry מדורג עם exponential backoff ו-dead letter queue.

**ערך עסקי:** הפחתת הודעות אבודות, שיפור אמינות המערכת.

**יישום מוצע:**
- 3 ניסיונות עם backoff: 30 שניות, 2 דקות, 10 דקות
- אחרי 3 כשלונות — העברה ל-dead letter queue
- דשבורד לצפייה בהודעות שנכשלו + retry ידני

---

### פיצ'ר 4: Webhook Signature Verification מלא

**תיאור:** חיזוק אימות חתימות webhook מכל הספקים (Telegram, WhatsApp Cloud, WPPConnect).

**ערך עסקי:** מניעת הזרקת הודעות מזויפות.

**יישום מוצע:**
- middleware ייעודי לכל ספק
- לוג לכל בקשה עם חתימה לא תקינה
- חסימת IP אחרי X ניסיונות כושלים

---

### פיצ'ר 5: Cache Layer לשאילתות תכופות

**תיאור:** שכבת cache ב-Redis לנתונים שנקראים הרבה ומשתנים מעט (פרופיל משתמש, הגדרות תחנה, רשימת ערים).

**ערך עסקי:** הפחתת עומס על DB, שיפור זמני תגובה.

**יישום מוצע:**
- decorator `@cached(ttl=300)` לפונקציות שירות
- invalidation אוטומטי בעדכון נתון
- מדדי hit/miss rate

---

### פיצ'ר 6: מערכת Audit Log מקיפה

**תיאור:** הרחבת audit log קיים (מיגרציה 010) — תיעוד כל פעולה רגישה כולל שינויי הרשאות, פעולות ארנק, שינויי סטטוס.

**ערך עסקי:** ציות רגולטורי, יכולת חקירה, שקיפות.

**יישום מוצע:**
- SQLAlchemy event listeners על מודלים רגישים
- טבלת audit עם: actor, action, entity, old_value, new_value, timestamp
- API לצפייה ב-panel

---

### פיצ'ר 7: תמיכה בהודעות מתוזמנות

**תיאור:** אפשרות לשולח לתזמן משלוח לשעה עתידית, כולל תזכורות אוטומטיות לשליח.

**ערך עסקי:** גמישות לשולחים, חוויית משתמש משופרת.

**יישום מוצע:**
- שדה `scheduled_at` בטבלת deliveries
- Celery beat task לפרסום בזמן הנכון
- התראות: שעה לפני, 15 דקות לפני

---

### פיצ'ר 8: מיגרציה ל-Alembic

**תיאור:** החלפת מיגרציות SQL ידניות ב-Alembic — מעקב אוטומטי, rollback, version history.

**ערך עסקי:** בטיחות deployment, מניעת drift בסכמה.

**יישום מוצע:**
```bash
alembic init migrations
alembic revision --autogenerate -m "initial"
```
- בדיקת התאמה בין מודלים לסכמה ב-CI
- rollback אוטומטי בכשלון deployment

---

## תוכנית פעולה מתועדפת

### שלב 1 — מיידי (שבוע 1-2)

| # | משימה | חומרה | קובץ |
|---|--------|--------|------|
| 1 | תיקון `get_task_session()` — singleton engine per worker | קריטית | `database.py` |
| 2 | הוספת בדיקת רישום קיים ל-`_handle_initial()` בשולח ושליח | קריטית | `handlers.py` |
| 3 | הוספת `with_for_update()` לפעולות ארנק קריטיות | קריטית | `wallet_service.py` |
| 4 | מימוש handler חסר ל-`REGISTER_COLLECT_PHONE` | קריטית | `handlers.py` |
| 5 | הוספת `validate_against_existing()` ל-3 מתודות חסרות | קריטית | `driver_menu_service.py` |
| 6 | הוספת authorization checks לשירותי נהג | קריטית | `driver_search_service.py`, `driver_menu_service.py` |

### שלב 2 — טווח קצר (שבוע 3-4)

| # | משימה | חומרה | קובץ |
|---|--------|--------|------|
| 7 | תיקון חריגות שנבלעות ב-admin_notification_service | בינונית | `admin_notification_service.py` |
| 8 | הוספת eager loading למניעת N+1 | בינונית | `delivery_service.py`, `driver_menu_service.py` |
| 9 | הסרת אינדקסים כפולים על UNIQUE | בינונית | `user.py`, `delivery.py` |
| 10 | הרחבת Correlation ID ל-16 תווים | בינונית | `logging.py` |
| 11 | הוספת connection timeout ו-pool size ל-DB | בינונית | `database.py` |
| 12 | תיקון rate limiter — מניעת דליפת זיכרון | בינונית | `middleware.py` |
| 13 | הסתרת נתונים פיננסיים בשגיאות | בינונית | `exceptions.py` |

### שלב 3 — טווח בינוני (חודש 2)

| # | משימה | חומרה |
|---|--------|--------|
| 14 | כתיבת 6 קבצי בדיקות לשירותים קריטיים | גבוהה |
| 15 | כתיבת בדיקות ל-API routes חסרים | גבוהה |
| 16 | הוספת סף כיסוי קוד (80%) ל-CI | גבוהה |
| 17 | הוספת mypy ו-bandit ל-CI | בינונית |
| 18 | מיגרציה ל-Alembic | בינונית |
| 19 | הוספת state `ADD_SHIPMENT_DROPOFF_APARTMENT` לסדרן | בינונית |

### שלב 4 — טווח ארוך (חודש 3+)

| # | משימה |
|---|--------|
| 20 | מימוש Health Dashboard |
| 21 | ביטול אוטומטי של משלוחים לא נתפסים |
| 22 | Cache layer ב-Redis |
| 23 | Audit log מקיף |
| 24 | בדיקות property-based עם Hypothesis |
| 25 | בדיקות אינטגרציה עם PostgreSQL אמיתי |

---

## נספח: ממצאים חיוביים

המערכת כוללת מספר דפוסים מצוינים שכדאי לשמר:

1. **מסיכת טלפונים** — שימוש עקבי ב-`PhoneNumberValidator.mask()` בכל ה-webhooks
2. **ניתוב תפקידים** — כל `UserRole` מטופל במפורש ב-telegram.py ו-whatsapp.py עם fallback מתועד
3. **Background tasks** — שימוש עקבי ב-`background_tasks.add_task()`, ללא `asyncio.create_task()`
4. **OpenAPI** — כל ה-endpoints מתועדים עם summary ו-description
5. **DriverStateHandler** — מימוש מצוין של בדיקת רישום קיים ב-INITIAL, guard functions מלאים, regex מעוגן לכפתורים
6. **DispatcherStateHandler** — ניקוי context מלא ומאורגן עם sets ייעודיים לכל flow
7. **StationOwnerHandler** — guard function מקיף שמכסה את כל ה-STATION.* states
8. **Circuit Breaker** — מימוש מוצק עם singleton pattern ו-thread safety
9. **בדיקות תרחיש** — 7 תרחישי E2E מכסים flows עסקיים שלמים כולל concurrent capture
10. **Transactional Outbox** — דפוס נכון להבטחת שליחת הודעות

---

> מסמך זה נוצר על בסיס סריקה אוטומטית מעמיקה של כל שכבות הקוד. מומלץ לעדכן אותו בכל ספרינט.
