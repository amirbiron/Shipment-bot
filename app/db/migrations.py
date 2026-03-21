"""
מיגרציות DB מרכזיות - מקור אמת יחיד לכל שינויי סכמה.

משמש גם את ה-startup (main.py) וגם את ה-API endpoints (routes/migrations.py).
כל המיגרציות idempotent (בטוח להריץ מספר פעמים).
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.logging import get_logger

logger = get_logger(__name__)


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

    # הערה: עדכון enum userrole (הוספת station_owner) מתבצע ב-add_enum_values()
    # כי ALTER TYPE ... ADD VALUE דורש AUTOCOMMIT ולא יכול לרוץ בתוך טרנזקציה.


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


async def run_migration_006(conn: AsyncConnection) -> None:
    """מיגרציה 006 - שלב 4: זרימת אישור משלוח וכרטיס סגור."""

    # הוספת שדות אישור משלוח לטבלת deliveries
    await conn.execute(text("""
        ALTER TABLE deliveries
            ADD COLUMN IF NOT EXISTS requesting_courier_id BIGINT REFERENCES users(id),
            ADD COLUMN IF NOT EXISTS requested_at TIMESTAMP,
            ADD COLUMN IF NOT EXISTS approved_by_id BIGINT REFERENCES users(id),
            ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP,
            ADD COLUMN IF NOT EXISTS approval_decision VARCHAR(20);
    """))

    # אינדקס על שליח מבקש
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_deliveries_requesting_courier
        ON deliveries(requesting_courier_id);
    """))

    # הוספת שדות קבוצות לטבלת תחנות
    await conn.execute(text("""
        ALTER TABLE stations
            ADD COLUMN IF NOT EXISTS public_group_chat_id VARCHAR(100),
            ADD COLUMN IF NOT EXISTS private_group_chat_id VARCHAR(100),
            ADD COLUMN IF NOT EXISTS public_group_platform VARCHAR(20),
            ADD COLUMN IF NOT EXISTS private_group_platform VARCHAR(20);
    """))

    # הרחבת recipient_id ב-outbox — מזהי קבוצות יכולים לחרוג מ-50 תווים
    await conn.execute(text("""
        ALTER TABLE outbox_messages ALTER COLUMN recipient_id TYPE VARCHAR(100);
    """))


async def add_enum_values(engine: AsyncEngine) -> None:
    """
    הוספת ערכים חדשים ל-enum types קיימים.

    ALTER TYPE ... ADD VALUE לא יכול לרוץ בתוך טרנזקציה (PG < 12)
    ולא בתוך בלוק PL/pgSQL (כל הגרסאות).
    לכן מריצים בחיבור נפרד עם AUTOCOMMIT.
    IF NOT EXISTS נתמך מ-PG 9.3 ומונע race condition בין מספר instances.
    """
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        # SQLEnum(UserRole) ללא values_callable שולח את שם ה-member (STATION_OWNER)
        # ולא את ה-value (station_owner), לכן חייבים להוסיף באותיות גדולות.
        await conn.execute(text(
            "ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'STATION_OWNER'"
        ))
        logger.info("Ensured 'STATION_OWNER' exists in userrole enum")

        # iDriver: הוספת DRIVER לתפקידי משתמש
        await conn.execute(text(
            "ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'DRIVER'"
        ))
        logger.info("Ensured 'DRIVER' exists in userrole enum")

        # שלב 4: הוספת PENDING_APPROVAL לסטטוס משלוח
        # SQLEnum(DeliveryStatus) ללא values_callable שולח את שם ה-member (uppercase)
        await conn.execute(text(
            "ALTER TYPE deliverystatus ADD VALUE IF NOT EXISTS 'PENDING_APPROVAL'"
        ))
        logger.info("Ensured 'PENDING_APPROVAL' exists in deliverystatus enum")

        # הוספת ערכי AuditActionType חדשים — פיצ'ר audit מקיף
        new_audit_actions = [
            "DELIVERY_CAPTURED",
            "DELIVERY_RELEASED",
            "DELIVERY_REQUESTED",
            "DELIVERY_APPROVED",
            "DELIVERY_REJECTED",
            "WALLET_REFUND",
            "AUTO_BLACKLIST_ADDED",
            "DELIVERY_STATUS_CHANGED",
            "WALLET_DEBIT",
            "WALLET_CREDIT",
        ]
        for action_name in new_audit_actions:
            await conn.execute(text(
                f"ALTER TYPE auditactiontype ADD VALUE IF NOT EXISTS '{action_name}'"
            ))
        logger.info("Ensured new AuditActionType values exist in auditactiontype enum")


async def run_migration_007(conn: AsyncConnection) -> None:
    """מיגרציה 007 - שלב 5: מדיניות פיננסית וחסימה אוטומטית.

    הוספת courier_id ו-is_paid לטבלת חיובים ידניים לצורך:
    - קישור אמין של חיובים לשליחים במערכת (לחסימה אוטומטית)
    - מעקב סטטוס תשלום (לדוח גבייה מדויק)
    """
    # הוספת עמודת courier_id לחיובים ידניים
    await conn.execute(text("""
        ALTER TABLE manual_charges
            ADD COLUMN IF NOT EXISTS courier_id BIGINT REFERENCES users(id);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_manual_charges_courier
        ON manual_charges(courier_id);
    """))

    # הוספת עמודת is_paid למעקב תשלומים
    await conn.execute(text("""
        ALTER TABLE manual_charges
            ADD COLUMN IF NOT EXISTS is_paid BOOLEAN DEFAULT FALSE NOT NULL;
    """))

    # אינדקס חלקי על חיובים שלא שולמו - לביצועי שאילתות דוח גבייה וחסימה
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_manual_charges_unpaid
        ON manual_charges(station_id, courier_id) WHERE is_paid = FALSE;
    """))


async def run_migration_008(conn: AsyncConnection) -> None:
    """מיגרציה 008 - הוספת עמודת rejection_note לטבלת users (הערת דחייה מהמנהל)."""
    await conn.execute(text("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS rejection_note TEXT;
    """))


async def run_migration_009(conn: AsyncConnection) -> None:
    """מיגרציה 009 - הגדרות תחנה מורחבות (סעיף 8 - Issue #210).

    הוספת שדות: description, operating_hours, service_areas, logo_url לטבלת stations.
    """
    await conn.execute(text("""
        ALTER TABLE stations
            ADD COLUMN IF NOT EXISTS description VARCHAR(500),
            ADD COLUMN IF NOT EXISTS operating_hours JSONB,
            ADD COLUMN IF NOT EXISTS service_areas JSONB,
            ADD COLUMN IF NOT EXISTS logo_url VARCHAR(500);
    """))


async def run_migration_010(conn: AsyncConnection) -> None:
    """מיגרציה 010 - הגדרות חסימה אוטומטית מותאמות לתחנה (סעיף 10 - Issue #210).

    הוספת שדות: auto_block_enabled, auto_block_grace_months, auto_block_min_debt
    לטבלת stations. מאפשר לבעלי תחנות להגדיר ספי חסימה ותקופת חסד.
    """
    # הוספת עמודות
    await conn.execute(text("""
        ALTER TABLE stations
            ADD COLUMN IF NOT EXISTS auto_block_enabled BOOLEAN DEFAULT TRUE NOT NULL,
            ADD COLUMN IF NOT EXISTS auto_block_grace_months INTEGER DEFAULT 2 NOT NULL,
            ADD COLUMN IF NOT EXISTS auto_block_min_debt NUMERIC(10, 2) DEFAULT 0 NOT NULL;
    """))

    # check constraints — הגנה ברמת ה-DB על ערכים תקינים
    await conn.execute(text("""
        DO $$ BEGIN
            ALTER TABLE stations
                ADD CONSTRAINT chk_auto_block_grace_months
                CHECK (auto_block_grace_months BETWEEN 1 AND 12);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """))
    await conn.execute(text("""
        DO $$ BEGIN
            ALTER TABLE stations
                ADD CONSTRAINT chk_auto_block_min_debt
                CHECK (auto_block_min_debt >= 0);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """))

    # אינדקס חלקי למשימת הבדיקה היומית — סינון תחנות פעילות עם חסימה מופעלת
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_stations_active_auto_block
        ON stations(id) WHERE is_active = TRUE AND auto_block_enabled = TRUE;
    """))


async def run_migration_011(conn: AsyncConnection) -> None:
    """מיגרציה 011 - אינדקסים מורכבים לטבלת audit_logs לביצועי שאילתות פאנל.

    אינדקסים על צירופי עמודות נפוצים בסינון:
    - (station_id, action, created_at) — סינון לפי סוג פעולה בתחנה
    - (station_id, actor_user_id, created_at) — סינון לפי משתמש מבצע בתחנה
    - (target_user_id, created_at) — חיפוש לפי "על מי בוצעה הפעולה"
    """
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_audit_logs_station_action_created
        ON audit_logs(station_id, action, created_at DESC);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_audit_logs_station_actor_created
        ON audit_logs(station_id, actor_user_id, created_at DESC);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_audit_logs_target_user
        ON audit_logs(target_user_id, created_at DESC);
    """))


async def run_migration_012(conn: AsyncConnection) -> None:
    """מיגרציה 012 — iDriver סשן 1: שכבת נתונים בסיסית.

    יצירת 4 טבלאות חדשות:
    - driver_profiles — פרופיל אישי, רכב, אימות ומנוי
    - driver_search_settings — העדפות סינון חיפוש
    - driver_searches — חיפושים פעילים (מקסימום 9 למשתמש)
    - driver_sessions — ניהול סשן 24 שעות
    """

    # טבלה 1: driver_profiles
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS driver_profiles (
            id SERIAL PRIMARY KEY,
            user_id BIGINT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            birth_date DATE NOT NULL,
            vehicle_description TEXT NOT NULL,
            vehicle_category VARCHAR(50) NOT NULL,
            dress_code VARCHAR(50) NOT NULL,

            verification_status VARCHAR(50) NOT NULL DEFAULT 'unverified',

            subscription_status VARCHAR(50) NOT NULL DEFAULT 'trial',
            trial_starts_at TIMESTAMP,
            trial_expires_at TIMESTAMP,
            subscription_start_at TIMESTAMP,
            subscription_expires_at TIMESTAMP,

            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """))

    # idx_driver_profiles_user — לא נדרש: user_id הוא UNIQUE ו-PostgreSQL
    # יוצר אינדקס ייחודי אוטומטית לאכיפת האילוץ.

    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_driver_profiles_verification
        ON driver_profiles(verification_status);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_driver_profiles_subscription
        ON driver_profiles(subscription_status, subscription_expires_at);
    """))

    # טבלה 2: driver_search_settings
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS driver_search_settings (
            id SERIAL PRIMARY KEY,
            user_id BIGINT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            vehicle_type_filter VARCHAR(50) DEFAULT '7_seater',
            trip_type_filter VARCHAR(50) DEFAULT 'any_distance',
            show_deliveries BOOLEAN DEFAULT TRUE,
            upcoming_timeframe VARCHAR(50) DEFAULT 'all',

            future_only_enabled BOOLEAN DEFAULT FALSE,
            future_only_start_time TIME,

            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """))

    # idx_driver_search_settings_user — לא נדרש: user_id הוא UNIQUE ו-PostgreSQL
    # יוצר אינדקס ייחודי אוטומטית לאכיפת האילוץ.

    # טבלה 3: driver_searches
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS driver_searches (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            origin_city VARCHAR(100) NOT NULL,
            destination_city VARCHAR(100) NOT NULL,
            is_area_search BOOLEAN DEFAULT FALSE,

            latitude DECIMAL(10, 7),
            longitude DECIMAL(10, 7),

            status VARCHAR(50) DEFAULT 'active',

            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """))

    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_driver_searches_user
        ON driver_searches(user_id);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_driver_searches_status
        ON driver_searches(status);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_driver_searches_location
        ON driver_searches(origin_city, destination_city);
    """))

    # טבלה 4: driver_sessions
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS driver_sessions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            session_start_at TIMESTAMP DEFAULT NOW(),
            last_message_at TIMESTAMP DEFAULT NOW(),
            is_active BOOLEAN DEFAULT TRUE,

            reminder_sent_at TIMESTAMP,

            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """))

    # idx_driver_sessions_user — לא נדרש: user_id הוא UNIQUE ו-PostgreSQL
    # יוצר אינדקס ייחודי אוטומטית לאכיפת האילוץ.

    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_driver_sessions_active
        ON driver_sessions(is_active);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_driver_sessions_last_activity
        ON driver_sessions(last_message_at);
    """))


async def run_migration_013(conn: AsyncConnection) -> None:
    """מיגרציה 013 — iDriver סשן 9: טבלת נסיעות סדרן (dispatcher_rides).

    נסיעות שסדרן מפרסם למערכת ההפצה ומופיעות בתוצאות חיפוש נהגים.
    """
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS dispatcher_rides (
            id SERIAL PRIMARY KEY,
            dispatcher_id BIGINT NOT NULL REFERENCES users(id),
            station_id INTEGER NOT NULL REFERENCES stations(id),

            origin_city VARCHAR(100) NOT NULL,
            destination_city VARCHAR(100) NOT NULL,

            seats INTEGER NOT NULL,
            price NUMERIC(10, 2) NOT NULL,
            description TEXT,

            is_delivery BOOLEAN DEFAULT FALSE NOT NULL,

            status VARCHAR(50) DEFAULT 'open' NOT NULL,

            taken_by_user_id BIGINT REFERENCES users(id),
            taken_at TIMESTAMP,

            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """))

    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_dispatcher_rides_dispatcher
        ON dispatcher_rides(dispatcher_id);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_dispatcher_rides_station
        ON dispatcher_rides(station_id);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_dispatcher_rides_status
        ON dispatcher_rides(status);
    """))
    # אינדקס מורכב לחיפוש נסיעות פתוחות לפי מוצא/יעד
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_dispatcher_rides_open_cities
        ON dispatcher_rides(origin_city, destination_city) WHERE status = 'open';
    """))


async def run_migration_014(conn: AsyncConnection) -> None:
    """מיגרציה 014 — הרחבת טבלת audit_logs למערכת audit מקיפה.

    שינויים:
    - station_id הופך ל-nullable (פעולות שלא קשורות לתחנה: אישור שליח, ארנק)
    - הוספת שדות entity_type, entity_id לזיהוי הישות שהשתנתה
    - הוספת שדות old_value, new_value למעקב שינויים מפורט
    - אינדקס מורכב על (entity_type, entity_id, created_at)
    """
    # הפיכת station_id ל-nullable — פעולות כמו אישור שליח לא קשורות לתחנה
    await conn.execute(text("""
        ALTER TABLE audit_logs ALTER COLUMN station_id DROP NOT NULL;
    """))

    # הוספת עמודות חדשות
    await conn.execute(text("""
        ALTER TABLE audit_logs
            ADD COLUMN IF NOT EXISTS entity_type VARCHAR(50),
            ADD COLUMN IF NOT EXISTS entity_id BIGINT,
            ADD COLUMN IF NOT EXISTS old_value JSONB,
            ADD COLUMN IF NOT EXISTS new_value JSONB;
    """))

    # אינדקס לחיפוש לפי ישות
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_audit_logs_entity_type
        ON audit_logs(entity_type);
    """))
    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_audit_logs_entity
        ON audit_logs(entity_type, entity_id, created_at DESC);
    """))

    # המרת details מ-JSON ל-JSONB לעקביות עם שאר העמודות
    await conn.execute(text("""
        ALTER TABLE audit_logs
            ALTER COLUMN details TYPE JSONB USING details::jsonb;
    """))


async def run_migration_015(conn: AsyncConnection) -> None:
    """מיגרציה 015 — הוספת עמודות תפוגה למשלוחים + username לטלגרם.

    שינויים:
    - deliveries.expires_at — זמן תפוגה לביטול אוטומטי של משלוחים שלא נתפסו
    - deliveries.expiry_warning_sent — האם נשלחה התראה לפני ביטול
    - users.telegram_username — @username בטלגרם לזיהוי בהודעות אדמין
    """
    await conn.execute(text("""
        ALTER TABLE deliveries
            ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP,
            ADD COLUMN IF NOT EXISTS expiry_warning_sent TIMESTAMP;
    """))

    await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_deliveries_expires_at
        ON deliveries(expires_at);
    """))

    await conn.execute(text("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS telegram_username VARCHAR(100);
    """))


async def run_all_migrations(conn: AsyncConnection) -> None:
    """הרצת כל המיגרציות ברצף (ללא ALTER TYPE — ראה add_enum_values)."""
    logger.info("Running migration 001...")
    await run_migration_001(conn)
    logger.info("Running migration 002...")
    await run_migration_002(conn)
    logger.info("Running migration 003...")
    await run_migration_003(conn)
    logger.info("Running migration 004...")
    await run_migration_004(conn)
    logger.info("Running migration 005...")
    await run_migration_005(conn)
    logger.info("Running migration 006...")
    await run_migration_006(conn)
    logger.info("Running migration 007...")
    await run_migration_007(conn)
    logger.info("Running migration 008...")
    await run_migration_008(conn)
    logger.info("Running migration 009...")
    await run_migration_009(conn)
    logger.info("Running migration 010...")
    await run_migration_010(conn)
    logger.info("Running migration 011...")
    await run_migration_011(conn)
    logger.info("Running migration 012...")
    await run_migration_012(conn)
    logger.info("Running migration 013...")
    await run_migration_013(conn)
    logger.info("Running migration 014...")
    await run_migration_014(conn)
    logger.info("Running migration 015...")
    await run_migration_015(conn)
