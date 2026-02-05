"""
Database migration endpoints - הרצה חד-פעמית להוספת שדות
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.migrations import run_migration_001, run_migration_002

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
        # AsyncSession.connection() מחזיר את ה-connection הפעיל של הסשן
        conn = await db.connection()
        await run_migration_001(conn)
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
        conn = await db.connection()
        await run_migration_002(conn)
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
