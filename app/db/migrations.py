"""
מיגרציות DB מרכזיות - מקור אמת יחיד לכל שינויי סכמה.

משמש גם את ה-startup (main.py) וגם את ה-API endpoints (routes/migrations.py).
כל המיגרציות idempotent (בטוח להריץ מספר פעמים).
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def run_migration_001(conn: AsyncConnection) -> None:
    """מיגרציה 001 - שדות הרשמת שליחים + עדכון credit_limit default."""
    # יצירת enum type לסטטוס אישור
    await conn.execute(text("""
        DO $$ BEGIN
            CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected', 'blocked');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """))

    # הוספת עמודות הרשמה לטבלת users
    await conn.execute(text("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS full_name VARCHAR(150),
            ADD COLUMN IF NOT EXISTS approval_status approval_status,
            ADD COLUMN IF NOT EXISTS id_document_url TEXT,
            ADD COLUMN IF NOT EXISTS service_area VARCHAR(100),
            ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMP;
    """))

    # אינדקס על סטטוס אישור
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_users_approval_status ON users(approval_status);
    """))

    # עדכון ברירת מחדל של credit_limit בטבלת courier_wallets
    await conn.execute(text("""
        ALTER TABLE courier_wallets ALTER COLUMN credit_limit SET DEFAULT -500.00;
    """))


async def run_migration_002(conn: AsyncConnection) -> None:
    """מיגרציה 002 - שדות KYC לשליחים (סלפי, קטגוריית רכב, תמונת רכב)."""
    await conn.execute(text("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS selfie_file_id TEXT,
            ADD COLUMN IF NOT EXISTS vehicle_category VARCHAR(50),
            ADD COLUMN IF NOT EXISTS vehicle_photo_file_id TEXT;
    """))


async def run_migration_003(conn: AsyncConnection) -> None:
    """מיגרציה 003 - טבלאות תחנות, סדרנים, ארנק תחנה, חיובים ידניים ורשימה שחורה [שלב 3]."""

    # טבלת תחנות
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS stations (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            owner_id BIGINT NOT NULL REFERENCES users(id),
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """))

    # טבלת סדרנים - קישור סדרן לתחנה
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS station_dispatchers (
            id SERIAL PRIMARY KEY,
            station_id INTEGER NOT NULL REFERENCES stations(id),
            user_id BIGINT NOT NULL REFERENCES users(id),
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT uq_station_dispatcher UNIQUE (station_id, user_id)
        );
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_station_dispatchers_station ON station_dispatchers(station_id);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_station_dispatchers_user ON station_dispatchers(user_id);
    """))

    # ארנק תחנה
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS station_wallets (
            id SERIAL PRIMARY KEY,
            station_id INTEGER NOT NULL REFERENCES stations(id) UNIQUE,
            balance FLOAT DEFAULT 0.0,
            commission_rate FLOAT DEFAULT 0.10,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """))

    # יצירת enum type לתנועות ארנק תחנה
    await conn.execute(text("""
        DO $$ BEGIN
            CREATE TYPE station_ledger_entry_type AS ENUM ('commission_credit', 'manual_charge', 'withdrawal');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """))

    # היסטוריית תנועות ארנק תחנה
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS station_ledger (
            id SERIAL PRIMARY KEY,
            station_id INTEGER NOT NULL REFERENCES stations(id),
            delivery_id INTEGER REFERENCES deliveries(id),
            entry_type station_ledger_entry_type NOT NULL,
            amount FLOAT NOT NULL,
            balance_after FLOAT NOT NULL,
            description VARCHAR(500),
            created_at TIMESTAMP DEFAULT NOW()
        );
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_station_ledger_station ON station_ledger(station_id);
    """))

    # חיובים ידניים
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS manual_charges (
            id SERIAL PRIMARY KEY,
            station_id INTEGER NOT NULL REFERENCES stations(id),
            dispatcher_id BIGINT NOT NULL REFERENCES users(id),
            driver_name VARCHAR(200) NOT NULL,
            amount FLOAT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_manual_charges_station ON manual_charges(station_id);
    """))

    # רשימה שחורה ברמת תחנה
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS station_blacklist (
            id SERIAL PRIMARY KEY,
            station_id INTEGER NOT NULL REFERENCES stations(id),
            courier_id BIGINT NOT NULL REFERENCES users(id),
            reason VARCHAR(500),
            consecutive_unpaid_months INTEGER DEFAULT 2,
            blocked_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT uq_station_blacklist UNIQUE (station_id, courier_id)
        );
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_station_blacklist_station ON station_blacklist(station_id);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_station_blacklist_courier ON station_blacklist(courier_id);
    """))

    # הוספת עמודת station_id לטבלת deliveries
    await conn.execute(text("""
        ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS station_id INTEGER REFERENCES stations(id);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_deliveries_station ON deliveries(station_id);
    """))

    # עדכון enum של UserRole - הוספת station_owner
    # הערה: ALTER TYPE ... ADD VALUE לא ניתן להרצה בתוך בלוק DO/PL/pgSQL,
    # לכן מריצים כ-statement רגיל. IF NOT EXISTS דורש PG 12+.
    await conn.execute(text(
        "ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'station_owner'"
    ))


async def run_migration_004(conn: AsyncConnection) -> None:
    """
    מיגרציה 004 - אינדקסים ליציבות Telegram.

    מוסיפה אינדקס על users.telegram_chat_id לביצועים.
    אם אין כפילויות - מוסיפה גם UNIQUE index כדי למנוע הישנות.

    הערה: אם יש כפילויות בפרודקשן, יצירת UNIQUE תיכשל ולכן אנחנו בודקים מראש.
    """
    # אינדקס רגיל (לא ייחודי) תמיד בטוח
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_users_telegram_chat_id
        ON users(telegram_chat_id)
        WHERE telegram_chat_id IS NOT NULL;
    """))

    # יצירת UNIQUE index רק אם אין כפילויות
    await conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM users
                WHERE telegram_chat_id IS NOT NULL
                GROUP BY telegram_chat_id
                HAVING COUNT(*) > 1
                LIMIT 1
            ) THEN
                EXECUTE '
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_users_telegram_chat_id_not_null
                    ON users(telegram_chat_id)
                    WHERE telegram_chat_id IS NOT NULL
                ';
            END IF;
        END $$;
    """))


async def run_migration_005(conn: AsyncConnection) -> None:
    """מיגרציה 005 - טבלת idempotency למניעת עיבוד כפול של הודעות webhook."""
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS webhook_events (
            message_id VARCHAR(200) PRIMARY KEY,
            platform VARCHAR(20) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'processing',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
    """))

    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_webhook_events_status_created
        ON webhook_events(status, created_at);
    """))


async def run_all_migrations(conn: AsyncConnection) -> None:
    """הרצת כל המיגרציות ברצף."""
    await run_migration_001(conn)
    await run_migration_002(conn)
    await run_migration_003(conn)
    await run_migration_004(conn)
    await run_migration_005(conn)
