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


async def run_all_migrations(conn: AsyncConnection) -> None:
    """הרצת כל המיגרציות ברצף."""
    await run_migration_001(conn)
    await run_migration_002(conn)
