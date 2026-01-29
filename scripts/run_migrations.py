#!/usr/bin/env python3
"""
Database Migration Runner

Runs all pending SQL migrations in order.
Tracks executed migrations in a `schema_migrations` table.
"""
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_connection():
    """Get database connection from DATABASE_URL"""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    # Render uses postgres:// but psycopg2 needs postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    return psycopg2.connect(database_url)


def ensure_migrations_table(conn):
    """Create migrations tracking table if not exists"""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                filename VARCHAR(255) UNIQUE NOT NULL,
                executed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()


def get_executed_migrations(conn):
    """Get list of already executed migrations"""
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM schema_migrations ORDER BY filename")
        return {row[0] for row in cur.fetchall()}


def get_pending_migrations(migrations_dir: Path, executed: set):
    """Get list of pending migration files"""
    if not migrations_dir.exists():
        return []

    migrations = []
    for f in sorted(migrations_dir.glob("*.sql")):
        if f.name not in executed:
            migrations.append(f)
    return migrations


def run_migration(conn, migration_file: Path):
    """Execute a single migration file"""
    print(f"  Running: {migration_file.name}")

    sql = migration_file.read_text()

    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (filename) VALUES (%s)",
            (migration_file.name,)
        )
    conn.commit()
    print(f"  ✓ Completed: {migration_file.name}")


def main():
    print("=" * 50)
    print("Database Migration Runner")
    print("=" * 50)

    # Find migrations directory
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    migrations_dir = project_root / "migrations"

    print(f"Migrations directory: {migrations_dir}")

    # Connect to database
    print("Connecting to database...")
    conn = get_db_connection()

    try:
        # Ensure migrations table exists
        ensure_migrations_table(conn)

        # Get executed migrations
        executed = get_executed_migrations(conn)
        print(f"Already executed: {len(executed)} migrations")

        # Get pending migrations
        pending = get_pending_migrations(migrations_dir, executed)

        if not pending:
            print("✓ No pending migrations")
            return 0

        print(f"Pending migrations: {len(pending)}")

        # Run each migration
        for migration_file in pending:
            run_migration(conn, migration_file)

        print("=" * 50)
        print(f"✓ Successfully ran {len(pending)} migrations")
        return 0

    except Exception as e:
        print(f"ERROR: Migration failed: {e}")
        conn.rollback()
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
