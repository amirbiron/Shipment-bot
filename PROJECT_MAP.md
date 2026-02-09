# מפת הפרויקט - Shipment Bot

> מסמך זה מיועד למתכנתים חדשים שמצטרפים לפרויקט.
> לכל קובץ יש תיאור קצר שמסביר מה תפקידו.

---

## קבצי שורש

| קובץ | תיאור |
|---|---|
| `app/main.py` | נקודת הכניסה של האפליקציה — הגדרת FastAPI, middleware, CORS, ותיעוד Swagger/ReDoc |
| `schema.sql` | סכמת מסד הנתונים ב-SQL |
| `Dockerfile` | בניית Docker image לאפליקציית FastAPI (Python 3.11) |
| `docker-compose.yml` | אורקסטרציה של כל השירותים — API, workers, Redis, PostgreSQL, WhatsApp Gateway |
| `render.yaml` | הגדרות deploy ל-Render (שירותי web, workers, Redis, PostgreSQL) |
| `requirements.txt` | תלויות Python של הפרויקט |
| `requirements-dev.txt` | תלויות פיתוח (pytest, mypy וכו') |
| `.env.example` | תבנית למשתני סביבה — יש להעתיק ל-`.env` ולמלא ערכים |
| `pytest.ini` | הגדרות pytest (asyncio, markers) |
| `mypy.ini` | הגדרות בדיקת טיפוסים |

### תיעוד
| קובץ | תיאור |
|---|---|
| `README.md` | סקירה כללית, התקנה מהירה, ותרשים ארכיטקטורה |
| `CLAUDE.md` | הנחיות פיתוח וסטנדרטים לקוד (חובה לקרוא!) |
| `ARCHITECTURE.md` | ארכיטקטורת המערכת — 4 שכבות, outbox pattern, state machine |
| `STATE_MACHINE.md` | תרשימי מצבים לזרימת שיחה של שולח ושליח |
| `DATABASE.md` | תיעוד סכמת מסד הנתונים, דוגמאות SQL, ודפוסי נעילת שורות |
| `CODE_REVIEW.md` | הנחיות לסקירת קוד |
| `API_DOCS_GUIDE.md` | מדריך לתיעוד API endpoints |
| `DEPLOYMENT.md` | הוראות העלאה לסביבת ייצור (Render) |

### סקריפטים (`scripts/`)
| קובץ | תיאור |
|---|---|
| `scripts/run_migrations.py` | הרצת מיגרציות מסד נתונים |
| `scripts/health_check.py` | בדיקת בריאות השירותים |
| `scripts/run_render_checks.sh` | בדיקות לפני deploy ב-Render |
| `scripts/smoke_webhooks.py` | בדיקת עשן ל-webhooks |

---

## שכבת Core — `app/core/`

תשתיות וכלים רוחביים שמשמשים את כל המערכת.

| קובץ | תיאור |
|---|---|
| `config.py` | הגדרות אפליקציה (Pydantic Settings) — כתובות DB, Redis, טוקנים, מגבלות אשראי |
| `logging.py` | מערכת לוגים מובנית (JSON) עם correlation IDs ו-decorators למדידת ביצועים |
| `validation.py` | ולידטורים — טלפון, כתובת, שם, סכומים, וזיהוי הזרקות (SQL/XSS) |
| `exceptions.py` | היררכיית exceptions מותאמים עם קודי שגיאה (DeliveryNotFoundError, InsufficientCreditError וכו') |
| `circuit_breaker.py` | מימוש Circuit Breaker להגנה על קריאות לשירותים חיצוניים (Telegram, WhatsApp) |
| `middleware.py` | middleware לבקשות HTTP — correlation IDs, לוגים, וטיפול גלובלי בשגיאות |

---

## שכבת מסד נתונים — `app/db/`

חיבור למסד הנתונים ומודלים של ORM.

| קובץ | תיאור |
|---|---|
| `database.py` | הגדרת SQLAlchemy async engine ו-session makers (אחד ל-API ואחד ל-Celery) |
| `migrations.py` | פונקציות מיגרציה — הוספת שדות שליח, שדות KYC |

### מודלים — `app/db/models/`

| קובץ | תיאור |
|---|---|
| `user.py` | מודל משתמש — שולחים ושליחים, תפקידים, סטטוס אישור, שדות KYC (מסמך, סלפי, קטגוריית רכב) |
| `delivery.py` | מודל משלוח — טוקן ל-smart links, פרטי איסוף/מסירה, מעקב סטטוס, שיוך שליח |
| `courier_wallet.py` | ארנק שליח — יתרה ומגבלת אשראי |
| `wallet_ledger.py` | ספר חשבונות (immutable) — היסטוריית עסקאות עם מניעת כפל חיוב |
| `outbox_message.py` | Transactional Outbox — הודעות ממתינות לשליחה אסינכרונית עם ספירת ניסיונות |
| `conversation_session.py` | מעקב אחר מצב מכונת המצבים בשיחה, כולל נתוני הקשר |
| `webhook_event.py` | טבלת idempotency — מניעת עיבוד כפול של הודעות webhook (message_id, status, created_at) |

---

## שכבת Domain — `app/domain/services/`

הלוגיקה העסקית של המערכת.

| קובץ | תיאור |
|---|---|
| `delivery_service.py` | ניהול משלוחים — יצירה, שליפה, סימון כנמסר |
| `capture_service.py` | תפיסת משלוח אטומית — נעילת שורות (row-level locks) עם בדיקת אשראי |
| `wallet_service.py` | פעולות ארנק שליח — יצירה, בדיקת יתרה, חיוב, זיכוי |
| `outbox_service.py` | מימוש דפוס Transactional Outbox עם backoff מעריכי |
| `admin_notification_service.py` | התראות למנהלים על רישום שליחים חדשים — דרך Telegram/WhatsApp כולל העלאת קבצים |

---

## מכונת מצבים — `app/state_machine/`

ניהול זרימת השיחה עם המשתמשים.

| קובץ | תיאור |
|---|---|
| `states.py` | הגדרת מצבים (enums) ל-SenderState ו-CourierState עם כל המצבים והמעברים האפשריים |
| `manager.py` | StateManager — ניהול מעברי מצבים, שמירת session, ואחסון הקשר שיחה |
| `handlers.py` | handlers לטיפול בהודעות — מימוש הלוגיקה לכל מצב בזרימת שולח ושליח |

---

## שכבת API — `app/api/`

נקודות קצה HTTP ו-webhooks.

### Routes — `app/api/routes/`

| קובץ | תיאור |
|---|---|
| `deliveries.py` | REST endpoints למשלוחים — יצירה, רשימה, תפיסה, סימון כנמסר (עם ולידציה Pydantic) |
| `users.py` | REST endpoints למשתמשים — יצירה, קריאה, עדכון (עם ולידציית תפקיד) |
| `wallets.py` | REST endpoints לארנקות — יתרה, היסטוריה, בדיקת אשראי |
| `migrations.py` | endpoints פנימיים להרצת מיגרציות |

### Webhooks — `app/api/webhooks/`

| קובץ | תיאור |
|---|---|
| `telegram.py` | webhook של בוט Telegram — פענוח הודעות, יצירת משתמשים, הפעלת מכונת מצבים |
| `whatsapp.py` | webhook של WhatsApp — קבלת הודעות מה-Gateway, הפעלת מכונת מצבים |

---

## Workers — `app/workers/`

עיבוד משימות אסינכרוני (Celery).

| קובץ | תיאור |
|---|---|
| `celery_app.py` | הגדרת אפליקציית Celery עם Beat schedule — עיבוד outbox וניקוי תקופתי |
| `tasks.py` | משימות Celery — עיבוד הודעות outbox, שליחה דרך WhatsApp/Telegram, וניקוי הודעות ישנות |

---

## WhatsApp Gateway — `whatsapp_gateway/`

מיקרו-שירות נפרד ב-Node.js לחיבור WhatsApp.

| קובץ | תיאור |
|---|---|
| `index.js` | שרת Express — חיבור WhatsApp דרך WPPConnect, העברת הודעות ל-FastAPI, ייצור QR code, ניהול sessions |
| `package.json` | תלויות Node.js — WPPConnect, Express, CORS |
| `Dockerfile` | בניית Docker image לשירות Node.js |

---

## בדיקות — `tests/`

| קובץ | תיאור |
|---|---|
| `conftest.py` | fixtures של pytest — מסד נתונים אסינכרוני, mock לשירותים חיצוניים, factories לבדיקות |
| `test_validation.py` | בדיקות ולידציה — מספרי טלפון, כתובות, וזיהוי הזרקות |
| `test_logging.py` | בדיקות מערכת לוגים ו-correlation IDs |
| `test_circuit_breaker.py` | בדיקות מעברי מצב ב-circuit breaker והתאוששות |
| `test_api_deliveries.py` | בדיקות API endpoints של משלוחים |
| `test_api_users.py` | בדיקות API endpoints של משתמשים |
| `test_wallet_service.py` | בדיקות פעולות ארנק ומגבלות אשראי |
| `test_stages_1_2.py` | בדיקות זרימת רישום שליח (שלבים 1-2) |
| `test_outbox_backoff.py` | בדיקות backoff של transactional outbox |
| `test_admin_notification_service.py` | בדיקות שירות התראות למנהלים |
| `test_telegram_webhook_smoke.py` | בדיקות עשן ל-webhook של Telegram |
| `test_whatsapp_webhook_state.py` | בדיקות מכונת מצבים ב-webhook של WhatsApp |

---

## תרשים ארכיטקטורה מקוצר

```
Telegram / WhatsApp
        │
        ▼
   Webhooks (app/api/webhooks/)
        │
        ▼
   State Machine (app/state_machine/)
        │
        ▼
   Domain Services (app/domain/services/)
        │
        ├──▶ DB Models (app/db/models/)  ──▶  PostgreSQL
        │
        └──▶ Outbox ──▶ Celery Workers (app/workers/)
                              │
                              ├──▶ Telegram API
                              └──▶ WhatsApp Gateway (whatsapp_gateway/)
```

---

> **טיפ:** לפני שמתחילים לכתוב קוד, חובה לקרוא את `CLAUDE.md` — שם מפורטים כל הסטנדרטים והכללים שחייבים לעקוב אחריהם.
