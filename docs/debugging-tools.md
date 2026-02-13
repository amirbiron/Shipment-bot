# כלי דיבאגינג ודיאגנוסטיקה

מסמך זה מפרט את כל כלי הדיבאגינג, הניטור והדיאגנוסטיקה הזמינים במערכת.

---

## תוכן עניינים

1. [Health Checks — בדיקות בריאות](#1-health-checks--בדיקות-בריאות)
2. [Admin Debug API — כלי דיאגנוסטיקה לאדמין](#2-admin-debug-api--כלי-דיאגנוסטיקה-לאדמין)
3. [סקריפט בדיקות בריאות (CLI)](#3-סקריפט-בדיקות-בריאות-cli)

---

## 1. Health Checks — בדיקות בריאות

שני endpoints ציבוריים (ללא אימות) לניטור זמינות המערכת.

### `GET /health` — Liveness Probe

בדיקה קלה שהתהליך חי ומגיב. **לא בודק תלויות חיצוניות** — כדי למנוע restart מיותר בגלל כשלון DB/Redis זמני.

- משמש את Render/Load Balancer להחלטת restart
- מוגדר ב-`render.yaml` כ-`healthCheckPath`

**תשובה:**
```json
{ "status": "healthy" }
```

---

### `GET /health/ready` — Readiness Probe

בדיקה מקיפה (deep health check) של כל התלויות החיצוניות:

| תלות | מה נבדק |
|---|---|
| **Database** | שאילתת `SELECT 1` |
| **Redis** | פקודת `PING` |
| **WhatsApp Gateway** | בקשת HTTP ל-`/health` + בדיקת שדה `connected` |
| **Celery** | `PING` ל-broker (Redis) |

**תשובה תקינה (200):**
```json
{
  "status": "healthy",
  "db": "ok",
  "redis": "ok",
  "whatsapp_gateway": "ok",
  "celery": "ok"
}
```

**תשובה עם בעיה (503):**
```json
{
  "status": "degraded",
  "db": "ok",
  "redis": "error: redis_unavailable",
  "whatsapp_gateway": "ok",
  "celery": "ok"
}
```

> **אבטחה:** הודעות השגיאה מסוננות — לא נחשפים פרטי תשתית (כתובות, סיסמאות וכו').

---

## 2. Admin Debug API — כלי דיאגנוסטיקה לאדמין

### אימות

כל ה-endpoints תחת `/api/admin/debug/` דורשים header:

```
X-Admin-API-Key: <הערך של ADMIN_API_KEY מהגדרות הסביבה>
```

| קוד שגיאה | משמעות |
|---|---|
| `401` | חסר header `X-Admin-API-Key` |
| `403` | המפתח שגוי, או ש-`ADMIN_API_KEY` לא הוגדר בסביבה (הגישה חסומה לחלוטין) |

#### הגדרת המפתח

ב-Render Dashboard:
```
שירות: shipment-bot-api → Environment → Add Variable
Key:   ADMIN_API_KEY
Value: <תוצאה של openssl rand -hex 32>
```

---

### 2.1 סטטוס Circuit Breakers

```
GET /api/admin/debug/circuit-breakers
```

מחזיר את המצב הנוכחי של כל ה-circuit breakers הרשומים — שימושי לבדיקת זמינות שירותים חיצוניים.

**תשובה:**
```json
[
  {
    "service": "telegram",
    "state": "closed",
    "failure_count": 0,
    "success_count": 42,
    "half_open_calls": 0,
    "retry_after_seconds": 0.0
  },
  {
    "service": "whatsapp",
    "state": "open",
    "failure_count": 5,
    "success_count": 0,
    "half_open_calls": 0,
    "retry_after_seconds": 23.5
  },
  {
    "service": "whatsapp_admin",
    "state": "closed",
    "failure_count": 0,
    "success_count": 10,
    "half_open_calls": 0,
    "retry_after_seconds": 0.0
  }
]
```

**מצבים אפשריים:**

| מצב | משמעות |
|---|---|
| `closed` | תקין — קריאות עוברות כרגיל |
| `open` | חסום — יותר מדי כשלונות, קריאות נחסמות. `retry_after_seconds` מציין מתי ייפתח |
| `half_open` | בדיקה — מאפשר מספר מצומצם של קריאות לבדוק אם השירות חזר |

---

### 2.2 ניהול הודעות Outbox

#### סיכום כמותי

```
GET /api/admin/debug/outbox/summary
```

**תשובה:**
```json
{
  "pending": 3,
  "processing": 1,
  "sent": 1842,
  "failed": 5,
  "total": 1851
}
```

---

#### שליפת הודעות

```
GET /api/admin/debug/outbox/messages?message_status=failed&limit=50
```

| פרמטר | ברירת מחדל | תיאור |
|---|---|---|
| `message_status` | `failed` | סינון לפי סטטוס: `pending`, `processing`, `sent`, `failed` |
| `limit` | `50` | מספר הודעות מקסימלי (1–200) |

**תשובה:**
```json
[
  {
    "id": 789,
    "platform": "telegram",
    "recipient_id": "123456789",
    "message_type": "delivery_notification",
    "status": "failed",
    "retry_count": 3,
    "max_retries": 5,
    "last_error": "Timeout connecting to Telegram API",
    "next_retry_at": "2026-02-13T12:30:00",
    "created_at": "2026-02-13T12:00:00",
    "processed_at": null
  }
]
```

---

#### Retry ידני להודעה כושלת

```
POST /api/admin/debug/outbox/messages/{message_id}/retry
```

מאפס את סטטוס ההודעה ל-`pending` כדי שה-Celery worker ישלח אותה מחדש.

> **חשוב:** עובד רק על הודעות בסטטוס `failed`. הודעות בסטטוסים אחרים יחזירו שגיאה `400`.

**תשובה:**
```json
{
  "message_id": 789,
  "previous_status": "failed",
  "new_status": "pending",
  "retry_count": 3
}
```

---

### 2.3 דיבאגינג State Machine של משתמש

#### צפייה במצב נוכחי

```
GET /api/admin/debug/users/{user_id}/state?platform=telegram
```

| פרמטר | חובה | תיאור |
|---|---|---|
| `user_id` | כן (path) | מזהה המשתמש |
| `platform` | לא | `telegram` או `whatsapp`. ברירת מחדל: מחזיר את ה-session שעודכן לאחרונה |

**תשובה:**
```json
{
  "user_id": 42,
  "user_name": "ישראל ישראלי",
  "user_role": "courier",
  "platform": "telegram",
  "current_state": "COURIER_REGISTER_COLLECT_SELFIE",
  "context_data": {
    "name": "ישראל ישראלי",
    "document_file_id": "AgACAgIAAxk..."
  },
  "updated_at": "2026-02-13T10:15:00",
  "last_activity_at": "2026-02-13T10:15:00"
}
```

**מתי להשתמש:** כשמשתמש מדווח שהוא "תקוע" ולא מצליח להתקדם בזרימה.

---

#### איפוס כפוי של State Machine

```
POST /api/admin/debug/users/{user_id}/force-state
```

**גוף הבקשה:**
```json
{
  "platform": "telegram",
  "new_state": "COURIER_MENU",
  "clear_context": true
}
```

| שדה | חובה | תיאור |
|---|---|---|
| `platform` | כן | `telegram` או `whatsapp` |
| `new_state` | כן | שם ה-state החדש (למשל `COURIER_MENU`, `SENDER_MENU`) |
| `clear_context` | לא (ברירת מחדל: `true`) | האם לנקות את ה-context data של השיחה |

> **אזהרה:** פעולה זו **עוקפת ולידציית מעברים**. יש להשתמש בה רק כשמשתמש תקוע בזרימה שבורה ואין דרך אחרת לשחרר אותו.

**מתי להשתמש:**
- משתמש תקוע ב-state שלא מגיב
- באג גרם למעבר ל-state לא תקין
- צריך להחזיר משתמש לתפריט הראשי

---

## 3. סקריפט בדיקות בריאות (CLI)

סקריפט עצמאי שרץ מתוך ה-shell של Render (או מקומית) ובודק תקינות רכיבי המערכת.

### הרצה

```bash
# כל הבדיקות
python scripts/health_check.py

# בדיקות ספציפיות בלבד
python scripts/health_check.py --only validation,circuit_breaker
```

### בדיקות זמינות

| שם | מה נבדק |
|---|---|
| `config` | טעינת הגדרות, משתני סביבה חיוניים (`DATABASE_URL`) |
| `validation` | טלפון (תיקוף, נרמול, מיסוך), כתובת, זיהוי SQL injection, סניטציה |
| `circuit_breaker` | דפוס Singleton, thread safety, תאימות multi event-loop (Celery) |
| `logging` | יצירת logger, ייצור ושליפה של Correlation ID |
| `exceptions` | exceptions מותאמים עם error codes ופרטים |
| `database` | חיבור למסד הנתונים (`SELECT 1`) |

### פלט

הסקריפט מציג פלט צבעוני עם סיכום:
```
==========================================================
 בדיקות בריאות המערכת - 2026-02-13 10:00:00
==========================================================

▶ Configuration
  ✓ PASS Settings loaded
  ✓ PASS DATABASE_URL configured

▶ Input Validation
  ✓ PASS Phone validation (valid)
  ✓ PASS Phone validation (invalid rejected)
  ✓ PASS Phone normalization
  ...

==========================================================
 סיכום
==========================================================
✓ כל הבדיקות עברו בהצלחה! (15/15)
```

---

## סיכום מהיר

| כלי | כתובת / פקודה | אימות | שימוש עיקרי |
|---|---|---|---|
| Liveness | `GET /health` | ללא | Render health check |
| Readiness | `GET /health/ready` | ללא | בדיקת כל התלויות |
| Circuit Breakers | `GET /api/admin/debug/circuit-breakers` | API Key | ניטור זמינות שירותים |
| Outbox סיכום | `GET /api/admin/debug/outbox/summary` | API Key | כמות הודעות לפי סטטוס |
| Outbox הודעות | `GET /api/admin/debug/outbox/messages` | API Key | צפייה בהודעות כושלות |
| Outbox retry | `POST /api/admin/debug/outbox/messages/{id}/retry` | API Key | שליחה מחדש של הודעה |
| User state | `GET /api/admin/debug/users/{id}/state` | API Key | בדיקת משתמש תקוע |
| Force state | `POST /api/admin/debug/users/{id}/force-state` | API Key | שחרור משתמש תקוע |
| Health script | `python scripts/health_check.py` | — | בדיקת רכיבים מקומית |
