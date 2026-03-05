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
    יצירת סשן DB עבור Celery tasks.

    יוצר engine חדש per-task ומשחרר אותו בסיום — הכרחי כי Celery
    יוצר event loop חדש לכל task, ו-engine שנקשר ל-loop ישן ייכשל.
    dispose() עטוף ב-try/finally כדי להבטיח שחרור גם בשגיאה.
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

    try:
        async with task_session_maker() as session:
            try:
                yield session
            finally:
                await session.close()
    finally:
        await task_engine.dispose()
