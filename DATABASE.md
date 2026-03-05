# Database Schema - סכמת בסיס נתונים

## סקירה כללית

המערכת משתמשת ב-PostgreSQL עם הטבלאות הבאות:

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│    users     │────▶│  deliveries  │◀────│courier_wallet│
└──────────────┘     └──────────────┘     └──────────────┘
       │                    │                    │
       ├────────────────────┼────────────────────┤
       │                    │                    │
       ▼                    ▼                    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│conversation_ │     │   outbox_    │     │   wallet_    │
│  sessions    │     │  messages    │     │   ledger     │
└──────────────┘     └──────────────┘     └──────────────┘
       │
       ├──────────────────────────────────────────────┐
       │                                              │
       ▼                                              ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   driver_    │  │   driver_    │  │   driver_    │  │   driver_    │
│  profiles    │  │  searches    │  │search_settin │  │  sessions    │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
```

## טבלאות

### users - משתמשים

שומר את כל המשתמשים במערכת (שולחים ושליחים).

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| phone_number | VARCHAR(20) | מספר טלפון |
| telegram_chat_id | VARCHAR(50) | מזהה צ'אט טלגרם |
| name | VARCHAR(100) | שם המשתמש |
| role | ENUM | sender / courier / admin / driver |
| platform | VARCHAR(20) | whatsapp / telegram |
| is_active | BOOLEAN | האם פעיל |
| created_at | TIMESTAMP | תאריך יצירה |

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    phone_number VARCHAR(20) UNIQUE,
    telegram_chat_id VARCHAR(50) UNIQUE,
    name VARCHAR(100),
    role VARCHAR(20) NOT NULL DEFAULT 'sender',
    platform VARCHAR(20) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_platform ON users(platform);
```

### deliveries - משלוחים

שומר את כל המשלוחים במערכת.

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| sender_id | INTEGER | FK למשתמש שולח |
| courier_id | INTEGER | FK לשליח (nullable) |
| pickup_address | TEXT | כתובת איסוף |
| pickup_lat/lng | DECIMAL | קואורדינטות איסוף |
| dropoff_address | TEXT | כתובת יעד |
| dropoff_lat/lng | DECIMAL | קואורדינטות יעד |
| status | ENUM | open / captured / delivered / cancelled |
| fee | DECIMAL | עמלת משלוח |
| notes | TEXT | הערות |
| created_at | TIMESTAMP | תאריך יצירה |
| captured_at | TIMESTAMP | תאריך תפיסה |
| delivered_at | TIMESTAMP | תאריך מסירה |

```sql
CREATE TABLE deliveries (
    id SERIAL PRIMARY KEY,
    sender_id INTEGER REFERENCES users(id),
    courier_id INTEGER REFERENCES users(id),
    pickup_address TEXT NOT NULL,
    pickup_latitude DECIMAL(10, 8),
    pickup_longitude DECIMAL(11, 8),
    dropoff_address TEXT NOT NULL,
    dropoff_latitude DECIMAL(10, 8),
    dropoff_longitude DECIMAL(11, 8),
    status VARCHAR(20) DEFAULT 'open',
    fee DECIMAL(10, 2) DEFAULT 10.00,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    captured_at TIMESTAMP,
    delivered_at TIMESTAMP
);

CREATE INDEX idx_deliveries_status ON deliveries(status);
CREATE INDEX idx_deliveries_sender ON deliveries(sender_id);
CREATE INDEX idx_deliveries_courier ON deliveries(courier_id);
```

### courier_wallets - ארנקי שליחים

ארנק לכל שליח עם יתרה נוכחית.

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| courier_id | INTEGER | FK לשליח (unique) |
| balance | DECIMAL | יתרה נוכחית |
| credit_limit | DECIMAL | מגבלת קרדיט (שלילי) |
| updated_at | TIMESTAMP | עדכון אחרון |

```sql
CREATE TABLE courier_wallets (
    id SERIAL PRIMARY KEY,
    courier_id INTEGER UNIQUE REFERENCES users(id),
    balance DECIMAL(10, 2) DEFAULT 0.00,
    credit_limit DECIMAL(10, 2) DEFAULT -100.00,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### wallet_ledger - היסטוריית תנועות

רישום בלתי-הפיך של כל תנועות הארנק.

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| courier_id | INTEGER | FK לשליח |
| delivery_id | INTEGER | FK למשלוח (nullable) |
| type | ENUM | delivery_fee_debit / payment / bonus / refund |
| amount | DECIMAL | סכום (שלילי לחיוב) |
| balance_after | DECIMAL | יתרה לאחר התנועה |
| description | TEXT | תיאור |
| created_at | TIMESTAMP | תאריך |

```sql
CREATE TABLE wallet_ledger (
    id SERIAL PRIMARY KEY,
    courier_id INTEGER REFERENCES users(id),
    delivery_id INTEGER REFERENCES deliveries(id),
    type VARCHAR(30) NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    balance_after DECIMAL(10, 2) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- מניעת חיוב כפול
    UNIQUE(courier_id, delivery_id, type)
);

CREATE INDEX idx_ledger_courier ON wallet_ledger(courier_id);
CREATE INDEX idx_ledger_delivery ON wallet_ledger(delivery_id);
```

### conversation_sessions - מצבי שיחה

שומר את מצב השיחה הנוכחי של כל משתמש.

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| user_id | INTEGER | FK למשתמש |
| platform | VARCHAR(20) | whatsapp / telegram |
| current_state | VARCHAR(100) | מצב נוכחי |
| context | JSONB | נתוני הקשר |
| updated_at | TIMESTAMP | עדכון אחרון |

```sql
CREATE TABLE conversation_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    platform VARCHAR(20) NOT NULL,
    current_state VARCHAR(100) NOT NULL,
    context JSONB DEFAULT '{}',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(user_id, platform)
);
```

### outbox_messages - הודעות ממתינות

Transactional Outbox לשליחת הודעות אסינכרונית.

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| platform | ENUM | whatsapp / telegram |
| recipient_id | VARCHAR(50) | מזהה נמען |
| message_type | VARCHAR(50) | סוג הודעה |
| message_content | JSONB | תוכן ההודעה |
| status | ENUM | pending / processing / sent / failed |
| retry_count | INTEGER | מספר ניסיונות |
| max_retries | INTEGER | מקסימום ניסיונות |
| created_at | TIMESTAMP | תאריך יצירה |
| processed_at | TIMESTAMP | תאריך עיבוד |
| next_retry_at | TIMESTAMP | זמן ניסיון הבא |
| last_error | TEXT | שגיאה אחרונה |

```sql
CREATE TABLE outbox_messages (
    id SERIAL PRIMARY KEY,
    platform VARCHAR(20) NOT NULL,
    recipient_id VARCHAR(50) NOT NULL,
    message_type VARCHAR(50) NOT NULL,
    message_content JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    next_retry_at TIMESTAMP,
    last_error TEXT
);

CREATE INDEX idx_outbox_status ON outbox_messages(status);
CREATE INDEX idx_outbox_next_retry ON outbox_messages(next_retry_at);
```

### driver_profiles - פרופילי נהגים (iDriver)

פרופיל נהג כולל נתוני רישום, אימות ומנוי.

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| user_id | INTEGER | FK למשתמש (unique) |
| birth_date | DATE | תאריך לידה |
| vehicle_description | TEXT | תיאור רכב חופשי |
| vehicle_category | VARCHAR(30) | car / 4_seater / 7_seater / 8_plus / motorcycle / truck / van |
| dress_code | VARCHAR(30) | hassidic / ultra_orthodox / modern_orthodox / religious_elegant / mixed / secular |
| verification_status | VARCHAR(20) | unverified / pending / approved / rejected |
| verification_selfie_file_id | TEXT | מזהה קובץ סלפי (nullable) |
| verification_id_file_id | TEXT | מזהה קובץ תעודת זהות (nullable) |
| rejection_reason | TEXT | סיבת דחייה (nullable) |
| subscription_status | VARCHAR(20) | trial / active / expired / paused / cancelled |
| trial_starts_at | TIMESTAMP | תחילת ניסיון |
| trial_expires_at | TIMESTAMP | תפוגת ניסיון |
| subscription_start_at | TIMESTAMP | תחילת מנוי |
| subscription_expires_at | TIMESTAMP | תפוגת מנוי |
| created_at | TIMESTAMP | תאריך יצירה |
| updated_at | TIMESTAMP | עדכון אחרון |

```sql
CREATE TABLE driver_profiles (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    birth_date DATE,
    vehicle_description TEXT,
    vehicle_category VARCHAR(30),
    dress_code VARCHAR(30),
    verification_status VARCHAR(20) DEFAULT 'unverified',
    verification_selfie_file_id TEXT,
    verification_id_file_id TEXT,
    rejection_reason TEXT,
    subscription_status VARCHAR(20) DEFAULT 'trial',
    trial_starts_at TIMESTAMP,
    trial_expires_at TIMESTAMP,
    subscription_start_at TIMESTAMP,
    subscription_expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### driver_search_settings - הגדרות חיפוש נהג

הגדרות סינון חיפוש לכל נהג.

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| user_id | INTEGER | FK למשתמש (unique) |
| vehicle_type_filter | VARCHAR(30) | סוג רכב לסינון |
| trip_type_filter | VARCHAR(30) | short_distance / medium_distance / long_distance / any_distance / rides |
| show_deliveries | BOOLEAN | הצגת משלוחים |
| upcoming_timeframe | VARCHAR(20) | 1_hour / 2_hours / 5_hours / all |
| future_only_enabled | BOOLEAN | חיפוש עתידי בלבד |
| future_only_start_time | TIME | שעת התחלה (כש-future_only=True) |
| created_at | TIMESTAMP | תאריך יצירה |
| updated_at | TIMESTAMP | עדכון אחרון |

```sql
CREATE TABLE driver_search_settings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    vehicle_type_filter VARCHAR(30) DEFAULT '7_seater',
    trip_type_filter VARCHAR(30) DEFAULT 'any_distance',
    show_deliveries BOOLEAN DEFAULT TRUE,
    upcoming_timeframe VARCHAR(20) DEFAULT 'all',
    future_only_enabled BOOLEAN DEFAULT FALSE,
    future_only_start_time TIME,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### driver_searches - חיפושי נהגים

חיפושים פעילים של נהגים (מקסימום 9 לנהג).

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| user_id | INTEGER | FK למשתמש |
| origin_city | VARCHAR(100) | עיר מוצא (nullable) |
| destination_city | VARCHAR(100) | עיר יעד |
| is_area_search | BOOLEAN | חיפוש אזורי |
| latitude | NUMERIC(10,7) | קו רוחב (nullable) |
| longitude | NUMERIC(10,7) | קו אורך (nullable) |
| status | VARCHAR(20) | active / paused / deleted |
| created_at | TIMESTAMP | תאריך יצירה |
| updated_at | TIMESTAMP | עדכון אחרון |

```sql
CREATE TABLE driver_searches (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    origin_city VARCHAR(100),
    destination_city VARCHAR(100) NOT NULL,
    is_area_search BOOLEAN DEFAULT FALSE,
    latitude NUMERIC(10, 7),
    longitude NUMERIC(10, 7),
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_driver_searches_user ON driver_searches(user_id);
CREATE INDEX idx_driver_searches_status ON driver_searches(status);
```

### driver_sessions - סשנים של נהגים

מעקב אחרי סשן 24 שעות של נהג.

| עמודה | טיפוס | תיאור |
|-------|-------|-------|
| id | SERIAL | מזהה ייחודי |
| user_id | INTEGER | FK למשתמש (unique) |
| session_start_at | TIMESTAMP | תחילת סשן |
| last_message_at | TIMESTAMP | הודעה אחרונה |
| is_active | BOOLEAN | האם הסשן פעיל |
| reminder_sent_at | TIMESTAMP | זמן שליחת תזכורת (nullable) |
| created_at | TIMESTAMP | תאריך יצירה |
| updated_at | TIMESTAMP | עדכון אחרון |

```sql
CREATE TABLE driver_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    session_start_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    reminder_sent_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_driver_sessions_active ON driver_sessions(is_active);
```

## Row Locking לאטומיות

### תפיסת משלוח

```sql
BEGIN;

-- נעילת משלוח
SELECT * FROM deliveries
WHERE id = $1 AND status = 'open'
FOR UPDATE;

-- נעילת ארנק שליח
SELECT * FROM courier_wallets
WHERE courier_id = $2
FOR UPDATE;

-- בדיקת מגבלת קרדיט
-- balance - fee >= credit_limit

-- עדכון משלוח
UPDATE deliveries
SET status = 'captured', courier_id = $2, captured_at = NOW()
WHERE id = $1;

-- הוספה ל-ledger
INSERT INTO wallet_ledger (courier_id, delivery_id, type, amount, balance_after)
VALUES ($2, $1, 'delivery_fee_debit', -10.00, new_balance);

-- עדכון יתרה
UPDATE courier_wallets
SET balance = balance - 10.00, updated_at = NOW()
WHERE courier_id = $2;

COMMIT;
```

## מיגרציות

המערכת משתמשת ב-Alembic למיגרציות:

```bash
# יצירת מיגרציה חדשה
alembic revision --autogenerate -m "description"

# הרצת מיגרציות
alembic upgrade head

# חזרה אחורה
alembic downgrade -1
```

## גיבוי

```bash
# גיבוי מלא
pg_dump -h localhost -U user shipment_bot > backup.sql

# שחזור
psql -h localhost -U user shipment_bot < backup.sql
```
