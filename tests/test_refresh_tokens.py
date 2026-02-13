"""
בדיקות refresh tokens ו-endpoint רענון טוקן
"""
import pytest

from app.core.auth import (
    create_access_token,
    create_refresh_token,
    store_otp,
    verify_refresh_token,
)
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.user import UserRole


class TestRefreshTokenCore:
    """בדיקות ליבה של refresh tokens"""

    @pytest.mark.asyncio
    async def test_create_and_verify_refresh_token(self):
        """יצירה ואימות של refresh token"""
        token = await create_refresh_token(user_id=1, station_id=1, role="station_owner")
        assert token is not None
        assert len(token) > 10

        payload = await verify_refresh_token(token)
        assert payload is not None
        assert payload.user_id == 1
        assert payload.station_id == 1
        assert payload.role == "station_owner"

    @pytest.mark.asyncio
    async def test_refresh_token_is_single_use(self):
        """refresh token נמחק אחרי שימוש (rotation)"""
        token = await create_refresh_token(user_id=1, station_id=1, role="station_owner")

        # שימוש ראשון — מצליח
        payload = await verify_refresh_token(token)
        assert payload is not None

        # שימוש שני — נכשל (הטוקן נמחק)
        payload2 = await verify_refresh_token(token)
        assert payload2 is None

    @pytest.mark.asyncio
    async def test_invalid_refresh_token_returns_none(self):
        """refresh token לא תקין מחזיר None"""
        result = await verify_refresh_token("nonexistent-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_each_refresh_token_is_unique(self):
        """כל refresh token ייחודי"""
        tokens = set()
        for _ in range(10):
            token = await create_refresh_token(user_id=1, station_id=1, role="station_owner")
            tokens.add(token)
        assert len(tokens) == 10


class TestVerifyOTPReturnsRefreshToken:
    """בדיקות ש-verify-otp מחזיר refresh token"""

    @pytest.mark.asyncio
    async def test_verify_otp_returns_refresh_token(
        self, test_client, user_factory, db_session,
    ):
        """אימות OTP מחזיר גם access_token וגם refresh_token"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        await store_otp(user.id, "123456")

        response = await test_client.post("/api/panel/auth/verify-otp", json={
            "phone_number": "0501234567",
            "otp": "123456",
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"


class TestRefreshEndpoint:
    """בדיקות endpoint רענון טוקן"""

    @pytest.mark.asyncio
    async def test_refresh_returns_new_tokens(
        self, test_client, user_factory, db_session,
    ):
        """רענון טוקן — מחזיר access + refresh חדשים"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        # יצירת refresh token
        refresh = await create_refresh_token(
            user_id=user.id, station_id=station.id, role="station_owner",
        )

        response = await test_client.post("/api/panel/auth/refresh", json={
            "refresh_token": refresh,
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["station_name"] == "תחנת בדיקה"

    @pytest.mark.asyncio
    async def test_refresh_rotates_token(
        self, test_client, user_factory, db_session,
    ):
        """refresh token ישן לא עובד אחרי שימוש (rotation)"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        refresh = await create_refresh_token(
            user_id=user.id, station_id=station.id, role="station_owner",
        )

        # שימוש ראשון — מצליח
        response1 = await test_client.post("/api/panel/auth/refresh", json={
            "refresh_token": refresh,
        })
        assert response1.status_code == 200

        # שימוש שני באותו token — 401
        response2 = await test_client.post("/api/panel/auth/refresh", json={
            "refresh_token": refresh,
        })
        assert response2.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_invalid_token_rejected(self, test_client):
        """refresh token לא תקין — 401"""
        response = await test_client.post("/api/panel/auth/refresh", json={
            "refresh_token": "invalid-token-value-here",
        })
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_inactive_user_rejected(
        self, test_client, user_factory, db_session,
    ):
        """משתמש לא פעיל — 403 ברענון"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה מושבת",
            role=UserRole.STATION_OWNER,
            is_active=False,
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        refresh = await create_refresh_token(
            user_id=user.id, station_id=station.id, role="station_owner",
        )

        response = await test_client.post("/api/panel/auth/refresh", json={
            "refresh_token": refresh,
        })
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_refresh_ownership_mismatch_rejected(
        self, test_client, user_factory, db_session,
    ):
        """משתמש שאינו בעלים של התחנה — 403"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעלים אמיתי",
            role=UserRole.STATION_OWNER,
        )
        other_user = await user_factory(
            phone_number="+972502222222",
            name="משתמש אחר",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        # refresh token עם user_id של משתמש אחר אבל station_id של הבעלים
        refresh = await create_refresh_token(
            user_id=other_user.id, station_id=station.id, role="station_owner",
        )

        response = await test_client.post("/api/panel/auth/refresh", json={
            "refresh_token": refresh,
        })
        assert response.status_code == 403
