# ארכיטקטורת המערכת - System Architecture

## סקירה כללית

המערכת בנויה בארכיטקטורת 4 שכבות + תור משימות אסינכרוני:

```
┌─────────────────────────────────────────────────────────────┐
│                      Bot Gateway                             │
│              (קבלה ושליחת הודעות)                            │
├─────────────────────────────────────────────────────────────┤
│                   Application Layer                          │
│           (Wizards, State Machine, Flow Logic)              │
├─────────────────────────────────────────────────────────────┤
│                     Domain Layer                             │
│      (חוקים עסקיים: הגדרות תפיסה, חישובי קרדיט)            │
├─────────────────────────────────────────────────────────────┤
│                      Data Layer                              │
│              (טרנזקציות בסיס נתונים)                        │
├─────────────────────────────────────────────────────────────┤
│                     Task Queue                               │
│         (פעולות אסינכרוניות: שידורים, לוגים)               │
└─────────────────────────────────────────────────────────────┘
```

## שכבת Bot Gateway

אחראית על:
- קבלת הודעות מ-WhatsApp ו-Telegram
- תרגום לפורמט אחיד
- שליחת תגובות חזרה למשתמש

### WhatsApp Gateway
- שירות Node.js נפרד עם WPPConnect
- מתקשר עם FastAPI דרך HTTP
- מנהל session וחיבור ל-WhatsApp Web

### Telegram Webhook
- מקבל updates ישירות מ-Telegram Bot API
- משולב ב-FastAPI

## שכבת Application

### State Machine
מנהל את זרימת השיחה עם המשתמש:

```python
# מצבי שולח
SENDER.NEW → SENDER.REGISTER.COLLECT_NAME → SENDER.MENU
SENDER.MENU → SENDER.DELIVERY.COLLECT_PICKUP → ...

# מצבי שליח
COURIER.NEW → COURIER.REGISTER.COLLECT_NAME → COURIER.MENU
COURIER.MENU → COURIER.CAPTURE.CONFIRM → ...
```

### Session Management
- שמירת מצב שיחה ב-DB (conversation_sessions)
- Context עם נתונים זמניים (כתובות, בחירות)

## שכבת Domain

### Delivery Service
- יצירת משלוחים חדשים
- עדכון סטטוסים
- חיפוש משלוחים פתוחים

### Capture Service
- תפיסת משלוח ע"י שליח
- אטומיות: תפיסה + חיוב ארנק בטרנזקציה אחת
- מניעת תפיסה כפולה עם row locks

### Wallet Service
- ניהול יתרות שליחים
- בדיקת מגבלת קרדיט
- רישום תנועות ב-ledger

### Outbox Service
- Transactional Outbox Pattern
- הוספת הודעות לתור
- ניהול retries

## שכבת Data

### PostgreSQL
נבחר בגלל:
- תמיכה ב-row-level locks (SELECT FOR UPDATE)
- ACID transactions
- JSON support לנתוני context

### מודלים עיקריים
- `users` - משתמשים (שולחים/שליחים)
- `deliveries` - משלוחים
- `courier_wallets` - ארנקי שליחים
- `wallet_ledger` - היסטוריית תנועות
- `outbox_messages` - הודעות ממתינות
- `conversation_sessions` - מצבי שיחה

## Task Queue

### Celery + Redis
- עיבוד הודעות מה-Outbox
- שידור לשליחים
- ניקוי הודעות ישנות

### תהליך שליחת הודעה
```
1. טרנזקציה: יצירת משלוח + הוספה ל-outbox
2. Commit
3. Worker קורא מ-outbox
4. שליחה ל-WhatsApp/Telegram
5. עדכון סטטוס ל-SENT
```

## Transactional Outbox Pattern

מונע:
- שליחה כפולה במקרה של retry
- חסימת שרת מ-APIs איטיים
- אובדן הודעות

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│ Business │───▶│  Outbox  │───▶│  Worker  │
│  Logic   │    │  Table   │    │  (Celery)│
└──────────┘    └──────────┘    └────┬─────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
              ┌──────────┐    ┌──────────┐    ┌──────────┐
              │ WhatsApp │    │ Telegram │    │  Admin   │
              └──────────┘    └──────────┘    └──────────┘
```

## אטומיות תפיסת משלוח

```sql
BEGIN;
  -- נעילת משלוח
  SELECT * FROM deliveries WHERE id = ? FOR UPDATE;
  -- וידוא סטטוס OPEN

  -- נעילת ארנק
  SELECT * FROM courier_wallets WHERE courier_id = ? FOR UPDATE;
  -- חישוב יתרה עתידית
  -- דחייה אם מתחת למגבלת קרדיט

  -- עדכון משלוח ל-CAPTURED
  UPDATE deliveries SET status = 'CAPTURED', courier_id = ?;

  -- הוספת רשומה ל-ledger
  INSERT INTO wallet_ledger (courier_id, delivery_id, type, amount);

COMMIT;
```

## Constraints למניעת כפילויות

```sql
-- מניעת חיוב כפול
UNIQUE(courier_id, delivery_id, type) ON wallet_ledger
```

## סקלביליות

### Horizontal Scaling
- FastAPI: ריבוי instances מאחורי load balancer
- Celery: ריבוי workers
- WhatsApp Gateway: instance אחד (מגבלת WhatsApp)

### Database
- Connection pooling
- Read replicas לשאילתות קריאה
- Indexes על שדות חיפוש נפוצים
