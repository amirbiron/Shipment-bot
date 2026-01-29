"""
Database Connection and Session Management
"""
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()


async def get_db() -> AsyncSession:
    """Dependency for getting database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_task_session():
    """
    Create a fresh database session for Celery tasks.

    This creates a new engine and session bound to the current event loop,
    avoiding the "attached to a different loop" error that occurs when
    reusing module-level engines across different event loops in Celery workers.
    """
    task_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10
    )
    task_session_maker = async_sessionmaker(
        bind=task_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    async with task_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()

    await task_engine.dispose()
