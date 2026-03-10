"""
Alembic Environment — תמיכה באסינכרוני עם asyncpg

מטעין את URL של ה-DB מ-app.core.config ומשתמש ב-async engine
להרצת מיגרציות. תומך גם במצב offline (יצירת SQL בלבד).
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.database import Base

# ייבוא כל המודלים כדי ש-Base.metadata יכיר אותם ב-autogenerate
import app.db.models  # noqa: F401

# Alembic Config object
config = context.config

# לוגים מ-alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# MetaData של המודלים — בסיס ל-autogenerate
target_metadata = Base.metadata


def get_url() -> str:
    """שליפת DATABASE_URL מהגדרות האפליקציה"""
    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    """הרצת מיגרציות במצב offline — יצירת SQL בלבד, ללא חיבור DB."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """הרצת מיגרציות על חיבור DB קיים"""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """הרצת מיגרציות באופן אסינכרוני עם asyncpg"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """הרצת מיגרציות במצב online — חיבור אסינכרוני ל-DB"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
