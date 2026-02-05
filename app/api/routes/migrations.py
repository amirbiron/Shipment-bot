"""
Database migration endpoints - הרצה חד-פעמית להוספת שדות
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.db.database import get_db

router = APIRouter()


@router.api_route(
    "/run-migration-001",
    methods=["GET", "POST"],
    summary="הרצת מיגרציה 001 (שדות הרשמת שליחים)",
    description="מוסיף שדות הרשמת שליחים לטבלת users. בטוח להריץ מספר פעמים (משתמש ב-IF NOT EXISTS).",
)
async def run_courier_fields_migration(
    db: AsyncSession = Depends(get_db)
):
    """
    Add courier registration fields to users table.
    Safe to run multiple times (uses IF NOT EXISTS).
    """
    try:
        # Create enum type
        await db.execute(text("""
            DO $$ BEGIN
                CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected', 'blocked');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """))

        # Add columns
        await db.execute(text("""
            ALTER TABLE users
                ADD COLUMN IF NOT EXISTS full_name VARCHAR(150),
                ADD COLUMN IF NOT EXISTS approval_status approval_status,
                ADD COLUMN IF NOT EXISTS id_document_url TEXT,
                ADD COLUMN IF NOT EXISTS service_area VARCHAR(100),
                ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMP;
        """))

        # Create index
        await db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_users_approval_status ON users(approval_status);
        """))

        # Update credit limit default
        await db.execute(text("""
            ALTER TABLE courier_wallets ALTER COLUMN credit_limit SET DEFAULT -500.00;
        """))

        await db.commit()

        return {
            "success": True,
            "message": "Migration 001 completed successfully - courier fields added"
        }

    except Exception as e:
        await db.rollback()
        return {
            "success": False,
            "error": str(e)
        }


@router.api_route(
    "/run-migration-002",
    methods=["GET", "POST"],
    summary="הרצת מיגרציה 002 (שדות KYC לשליחים)",
    description="מוסיף שדות KYC חדשים לטבלת users: סלפי, קטגוריית רכב, תמונת רכב. בטוח להריץ מספר פעמים.",
)
async def run_kyc_fields_migration(
    db: AsyncSession = Depends(get_db)
):
    """
    הוספת שדות KYC חדשים לטבלת users [שלב 2].
    בטוח להריץ מספר פעמים (משתמש ב-IF NOT EXISTS).
    """
    try:
        await db.execute(text("""
            ALTER TABLE users
                ADD COLUMN IF NOT EXISTS selfie_file_id TEXT,
                ADD COLUMN IF NOT EXISTS vehicle_category VARCHAR(50),
                ADD COLUMN IF NOT EXISTS vehicle_photo_file_id TEXT;
        """))

        await db.commit()

        return {
            "success": True,
            "message": "Migration 002 completed successfully - KYC fields added (selfie_file_id, vehicle_category, vehicle_photo_file_id)"
        }

    except Exception as e:
        await db.rollback()
        return {
            "success": False,
            "error": str(e)
        }
