"""
Database migration endpoint - Run once to add courier fields
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.db.database import get_db

router = APIRouter()


@router.api_route("/run-migration-001", methods=["GET", "POST"])
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
                ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMP WITH TIME ZONE;
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
