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


# engine singleton לכל worker — מונע יצירת pool חדש בכל קריאה ל-get_task_session()
_task_engine = None
_task_session_maker = None


def _get_task_engine():
    """שליפת engine singleton עבור Celery worker.

    יוצר engine חדש רק בפעם הראשונה (per-worker).
    """
    global _task_engine, _task_session_maker
    if _task_engine is None:
        _task_engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DEBUG,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        _task_session_maker = async_sessionmaker(
            bind=_task_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _task_engine, _task_session_maker


@asynccontextmanager
async def get_task_session():
    """
    יצירת סשן DB עבור Celery tasks.

    משתמש ב-engine singleton per-worker כדי למנוע דליפת connection pools.
    """
    _, session_maker = _get_task_engine()

    async with session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
