"""
בדיקות אימות לפאנל ווב — OTP + JWT
"""
import pytest

from app.core.auth import create_access_token, verify_token, generate_otp, store_otp, verify_otp
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet


class TestOTPGeneration:
    """בדיקות יצירת OTP"""

    @pytest.mark.unit
    def test_generate_otp_is_6_digits(self):
        """OTP חייב להיות 6 ספרות"""
        otp = generate_otp()
        assert len(otp) == 6
        assert otp.isdigit()

    @pytest.mark.unit
    def test_generate_otp_is_random(self):
        """כל OTP צריך להיות שונה"""
        otps = {generate_otp() for _ in range(20)}
        assert len(otps) > 1


class TestJWT:
    """בדיקות JWT"""

    @pytest.mark.unit
    def test_create_and_verify_token(self):
        """יצירת token ואימות שלו"""
        token = create_access_token(user_id=123, station_id=1, role="station_owner")
        assert token is not None

        payload = verify_token(token)
        assert payload is not None
        assert payload.user_id == 123
        assert payload.station_id == 1
        assert payload.role == "station_owner"

    @pytest.mark.unit
    def test_invalid_token_returns_none(self):
        """token לא תקין מחזיר None"""
        result = verify_token("invalid.token.here")
        assert result is None


class TestOTPStorage:
    """בדיקות אחסון OTP ב-Redis"""

    @pytest.mark.asyncio
    async def test_store_and_verify_otp(self):
        """שמירה ואימות OTP"""
        await store_otp(user_id=123, otp="123456")
        result = await verify_otp(user_id=123, otp="123456")
        assert result is True

    @pytest.mark.asyncio
    async def test_wrong_otp_fails(self):
        """OTP שגוי נכשל"""
        await store_otp(user_id=123, otp="123456")
        result = await verify_otp(user_id=123, otp="000000")
        assert result is False

    @pytest.mark.asyncio
    async def test_otp_is_single_use(self):
        """OTP נמחק אחרי שימוש (one-time)"""
        await store_otp(user_id=123, otp="123456")
        result1 = await verify_otp(user_id=123, otp="123456")
        assert result1 is True
        result2 = await verify_otp(user_id=123, otp="123456")
        assert result2 is False


class TestPanelAuthEndpoints:
    """בדיקות API endpoints של אימות"""

    @pytest.mark.asyncio
    async def test_request_otp_valid_station_owner(
        self, test_client, user_factory, db_session,
    ):
        """בקשת OTP למשתמש שהוא בעל תחנה"""
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

        response = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0501234567",
        })
        assert response.status_code == 200
        assert "קוד כניסה נשלח" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_request_otp_non_owner_rejected(self, test_client, user_factory):
        """בקשת OTP למשתמש רגיל — נדחה"""
        await user_factory(
            phone_number="+972501234567",
            name="שולח רגיל",
            role=UserRole.SENDER,
        )
        response = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0501234567",
        })
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_request_otp_unknown_phone(self, test_client):
        """בקשת OTP לטלפון לא קיים — 404"""
        response = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0509999999",
        })
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_verify_otp_returns_jwt(
        self, test_client, user_factory, db_session,
    ):
        """אימות OTP תקין — מחזיר JWT token"""
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

        # שמירת OTP
        await store_otp(user.id, "123456")

        response = await test_client.post("/api/panel/auth/verify-otp", json={
            "phone_number": "0501234567",
            "otp": "123456",
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["station_name"] == "תחנת בדיקה"

    @pytest.mark.asyncio
    async def test_wrong_otp_rejected(
        self, test_client, user_factory, db_session,
    ):
        """OTP שגוי — 401"""
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
            "otp": "000000",
        })
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_dashboard_unauthorized(self, test_client):
        """גישה לדשבורד ללא token — 403"""
        response = await test_client.get("/api/panel/dashboard")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_me_with_valid_token(
        self, test_client, user_factory, db_session,
    ):
        """GET /me עם token תקין"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת מבחן", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        token = create_access_token(user.id, station.id, "station_owner")

        response = await test_client.get(
            "/api/panel/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["station_name"] == "תחנת מבחן"
