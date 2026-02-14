"""
Pytest Configuration and Fixtures

Provides fixtures for:
- Database sessions (async)
- Mock external services (Telegram, WhatsApp)
- Test data factories
"""
# הגדרת JWT_SECRET_KEY לפני ייבוא app — הולידטור דורש מפתח כש-DEBUG=False
import os
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-only-do-not-use-in-production")

import pytest
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import Response

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import event

from app.db.database import Base, get_db
from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.courier_wallet import CourierWallet
from app.core.config import settings
from app.main import app


# Test database URL (SQLite in memory for fast tests)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# הערה: לא מגדירים event_loop fixture מותאם אישית כי pytest-asyncio 0.23+
# מטפל בזה אוטומטית עם asyncio_mode=auto ו-asyncio_default_fixture_loop_scope=function


# SQLite לא תומך ב-autoincrement עבור BigInteger, לכן נוסיף event listener
# שמייצר ID אוטומטית לפני הכנסה
def _set_user_id_before_insert(mapper, connection, target):
    """מייצר ID ייחודי עבור User אם לא הוגדר - נדרש עבור SQLite"""
    if target.id is None:
        target.id = _get_next_test_id()


# רושמים את ה-listener על מודל User
event.listen(User, 'before_insert', _set_user_id_before_insert)


@pytest.fixture(scope="function")
async def async_engine():
    """Create async test database engine"""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture(scope="function")
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create async database session for tests"""
    async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture(scope="function")
async def test_client(db_session: AsyncSession):
    """Create test client with database override"""
    from httpx import AsyncClient, ASGITransport

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# ============================================================================
# Mock External Services
# ============================================================================

@pytest.fixture
def mock_telegram_api():
    """Mock Telegram Bot API responses"""
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True, "result": {}}

        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(return_value=mock_response)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)

        mock_client.return_value = mock_instance

        yield mock_instance


@pytest.fixture
def mock_whatsapp_gateway():
    """Mock WhatsApp Gateway API responses"""
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}

        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(return_value=mock_response)
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)

        mock_client.return_value = mock_instance

        yield mock_instance


@pytest.fixture
def mock_external_services(mock_telegram_api, mock_whatsapp_gateway):
    """Mock all external services"""
    return {
        "telegram": mock_telegram_api,
        "whatsapp": mock_whatsapp_gateway
    }


# ============================================================================
# Test Data Factories
# ============================================================================

# מונה גלובלי ל-ID עבור בדיקות - SQLite לא תומך ב-autoincrement עבור BigInteger
# מתחילים מ-10000 כדי למנוע התנגשויות עם IDs מפורשים שבדיקות עשויות להעביר
_test_id_counter = 10000


def _get_next_test_id() -> int:
    """מייצר ID ייחודי לבדיקות"""
    global _test_id_counter
    _test_id_counter += 1
    return _test_id_counter


@pytest.fixture(autouse=True)
def reset_test_id_counter():
    """מאפס את מונה ה-ID בין בדיקות"""
    global _test_id_counter
    _test_id_counter = 10000
    yield


@pytest.fixture
def user_factory(db_session: AsyncSession):
    """Factory for creating test users"""
    async def _create_user(
        phone_number: str = "+972501234567",
        name: str = "Test User",
        full_name: str | None = None,
        role: UserRole = UserRole.SENDER,
        platform: str = "whatsapp",
        telegram_chat_id: str | None = None,
        is_active: bool = True,
        approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
        id: int | None = None  # SQLite לא תומך ב-autoincrement עבור BigInteger
    ) -> User:
        user = User(
            id=id if id is not None else _get_next_test_id(),
            phone_number=phone_number,
            name=name,
            full_name=full_name,
            role=role,
            platform=platform,
            telegram_chat_id=telegram_chat_id,
            is_active=is_active,
            approval_status=approval_status
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    return _create_user


@pytest.fixture
def delivery_factory(db_session: AsyncSession):
    """Factory for creating test deliveries"""
    async def _create_delivery(
        sender_id: int,
        pickup_address: str = "רחוב הרצל 1, תל אביב",
        dropoff_address: str = "רחוב בן יהודה 50, ירושלים",
        status: DeliveryStatus = DeliveryStatus.OPEN,
        courier_id: int | None = None,
        fee: float = 10.0
    ) -> Delivery:
        delivery = Delivery(
            sender_id=sender_id,
            pickup_address=pickup_address,
            dropoff_address=dropoff_address,
            status=status,
            courier_id=courier_id,
            fee=fee
        )
        db_session.add(delivery)
        await db_session.commit()
        await db_session.refresh(delivery)
        return delivery

    return _create_delivery


@pytest.fixture
def wallet_factory(db_session: AsyncSession):
    """Factory for creating test wallets"""
    async def _create_wallet(
        courier_id: int,
        balance: float = 0.0,
        credit_limit: float = -500.0
    ) -> CourierWallet:
        wallet = CourierWallet(
            courier_id=courier_id,
            balance=balance,
            credit_limit=credit_limit
        )
        db_session.add(wallet)
        await db_session.commit()
        await db_session.refresh(wallet)
        return wallet

    return _create_wallet


# ============================================================================
# Sample Test Data
# ============================================================================

@pytest.fixture
async def sample_sender(user_factory) -> User:
    """Create a sample sender user"""
    return await user_factory(
        phone_number="+972501111111",
        name="Sample Sender",
        role=UserRole.SENDER
    )


@pytest.fixture
async def sample_courier(user_factory, wallet_factory) -> User:
    """Create a sample courier user with wallet"""
    courier = await user_factory(
        phone_number="+972502222222",
        name="Sample Courier",
        role=UserRole.COURIER,
        approval_status=ApprovalStatus.APPROVED
    )
    await wallet_factory(courier_id=courier.id, balance=100.0)
    return courier


@pytest.fixture
async def sample_delivery(delivery_factory, sample_sender) -> Delivery:
    """Create a sample open delivery"""
    return await delivery_factory(sender_id=sample_sender.id)


# ============================================================================
# Circuit Breaker Reset
# ============================================================================

@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """Reset circuit breakers between tests"""
    from app.core.circuit_breaker import CircuitBreaker
    CircuitBreaker.reset_all()
    yield
    CircuitBreaker.reset_all()


class FakeRedis:
    """תחליף ל-Redis לבדיקות — in-memory dict עם ממשק תואם ומעקב TTL."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        """SET עם תמיכה ב-NX (רק אם לא קיים) ו-EX (תפוגה בשניות)"""
        if nx and key in self._store:
            return None
        self._store[key] = value
        if ex is not None:
            self._ttls[key] = ex
        return True

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value
        self._ttls[key] = ttl

    async def getdel(self, key: str) -> str | None:
        self._ttls.pop(key, None)
        return self._store.pop(key, None)

    async def incr(self, key: str) -> int:
        """INCR אטומי — מגדיל ב-1, מאתחל ל-1 אם לא קיים"""
        current = self._store.get(key)
        new_val = int(current) + 1 if current is not None else 1
        self._store[key] = str(new_val)
        return new_val

    async def decr(self, key: str) -> int:
        """DECR אטומי — מקטין ב-1, מאתחל ל--1 אם לא קיים. שומר TTL."""
        current = self._store.get(key)
        new_val = int(current) - 1 if current is not None else -1
        self._store[key] = str(new_val)
        return new_val

    async def expire(self, key: str, ttl: int) -> None:
        """הגדרת TTL למפתח קיים"""
        if key in self._store:
            self._ttls[key] = ttl

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)
            self._ttls.pop(key, None)

    async def aclose(self) -> None:
        self._store.clear()
        self._ttls.clear()


@pytest.fixture(autouse=True)
def fake_redis():
    """מחליף את get_redis ב-FakeRedis לכל הבדיקות."""
    _fake = FakeRedis()

    async def _get_fake_redis():
        return _fake

    with patch("app.core.redis_client.get_redis", _get_fake_redis), \
         patch("app.core.auth.get_redis", _get_fake_redis):
        yield _fake


# ============================================================================
# JWT Secret for panel tests
# ============================================================================

_TEST_JWT_SECRET = "test-jwt-secret-key-for-testing-only-do-not-use-in-production"


@pytest.fixture(autouse=True)
def set_jwt_secret():
    """מגדיר JWT_SECRET_KEY לבדיקות פאנל"""
    with patch.object(settings, "JWT_SECRET_KEY", _TEST_JWT_SECRET), \
         patch.object(settings, "JWT_ALGORITHM", "HS256"), \
         patch.object(settings, "JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 480), \
         patch.object(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 30), \
         patch.object(settings, "OTP_EXPIRE_SECONDS", 300):
        yield


# הערה: אין צורך ב-autouse fixture לניקוי WebhookEvent (idempotency) —
# כל בדיקה מקבלת DB in-memory חדש דרך async_engine (function-scoped).
