"""
בדיקות אימות לפאנל ווב — OTP + JWT
"""
import pytest

from app.core.auth import (
    create_access_token,
    generate_otp,
    store_otp,
    try_set_otp_cooldown_by_phone,
    verify_otp,
    verify_token,
)
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
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_request_otp_non_owner_generic_response(self, test_client, user_factory):
        """בקשת OTP למשתמש רגיל — תשובה גנרית (מניעת user-enumeration)"""
        await user_factory(
            phone_number="+972501234567",
            name="שולח רגיל",
            role=UserRole.SENDER,
        )
        response = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0501234567",
        })
        # תשובה גנרית — לא חושפת שהמשתמש קיים אבל אינו בעל תחנה
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_request_otp_unknown_phone_generic_response(self, test_client):
        """בקשת OTP לטלפון לא קיים — תשובה גנרית (מניעת user-enumeration)"""
        response = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0509999999",
        })
        # תשובה גנרית — לא חושפת שהמספר לא קיים
        assert response.status_code == 200
        assert response.json()["success"] is True

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

    @pytest.mark.asyncio
    async def test_request_otp_rate_limit(
        self, test_client, user_factory, db_session,
    ):
        """בקשת OTP כפולה — 429"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        # בקשה ראשונה — הצלחה
        response1 = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0501234567",
        })
        assert response1.status_code == 200

        # בקשה שנייה מיידית — 429
        response2 = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0501234567",
        })
        assert response2.status_code == 429

    @pytest.mark.asyncio
    async def test_unknown_phone_also_rate_limited(self, test_client):
        """טלפון לא קיים גם מקבל 429 בבקשה כפולה — מונע enumeration"""
        # בקשה ראשונה — 200 גנרי
        response1 = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0508888888",
        })
        assert response1.status_code == 200

        # בקשה שנייה מיידית — 429 (כמו בטלפון תקין)
        response2 = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0508888888",
        })
        assert response2.status_code == 429

    @pytest.mark.asyncio
    async def test_ownership_mismatch_rejected(
        self, test_client, user_factory, db_session,
    ):
        """token עם user_id שאינו הבעלים — 403"""
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

        # token עם user_id של משתמש אחר אבל station_id של הבעלים
        token = create_access_token(other_user.id, station.id, "station_owner")
        response = await test_client.get(
            "/api/panel/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


class TestDeactivatedUser:
    """בדיקות שמשתמש לא פעיל נחסם בכל שלבי האימות"""

    @pytest.mark.asyncio
    async def test_inactive_user_request_otp_generic(
        self, test_client, user_factory, db_session,
    ):
        """משתמש לא פעיל — תשובה גנרית (מניעת user-enumeration)"""
        user = await user_factory(
            phone_number="+972503333333",
            name="מושבת",
            role=UserRole.STATION_OWNER,
            is_active=False,
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        response = await test_client.post("/api/panel/auth/request-otp", json={
            "phone_number": "0503333333",
        })
        # תשובה גנרית — לא חושפת שהמשתמש מושבת
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_inactive_user_dashboard_rejected(
        self, test_client, user_factory, db_session,
    ):
        """משתמש לא פעיל עם token תקין — 403 בגישה לדשבורד"""
        user = await user_factory(
            phone_number="+972504444444",
            name="מושבת עם טוקן",
            role=UserRole.STATION_OWNER,
            is_active=False,
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        # טוקן תקין — אבל המשתמש כבר לא פעיל
        token = create_access_token(user.id, station.id, "station_owner")
        response = await test_client.get(
            "/api/panel/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


class TestJWTExpTimestamp:
    """בדיקות שה-exp ב-JWT הוא int (Unix timestamp)"""

    @pytest.mark.unit
    def test_exp_is_int(self):
        """exp ב-TokenPayload חייב להיות int"""
        token = create_access_token(user_id=1, station_id=1, role="station_owner")
        payload = verify_token(token)
        assert payload is not None
        assert isinstance(payload.exp, int)


class TestOTPRateLimiting:
    """בדיקות rate limiting של OTP"""

    @pytest.mark.asyncio
    async def test_atomic_cooldown_blocks_rapid_requests(self):
        """cooldown אטומי (SET NX EX) חוסם בקשות מהירות"""
        phone = "+972501234567"
        # קריאה ראשונה — מותר
        first = await try_set_otp_cooldown_by_phone(phone)
        assert first is True
        # קריאה שנייה — חסום
        second = await try_set_otp_cooldown_by_phone(phone)
        assert second is False

    @pytest.mark.asyncio
    async def test_atomic_cooldown_allows_different_numbers(self):
        """cooldown על מספר אחד לא חוסם מספר אחר"""
        await try_set_otp_cooldown_by_phone("+972501111111")
        allowed = await try_set_otp_cooldown_by_phone("+972502222222")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_otp_max_attempts_exceeded(self):
        """חריגה ממקסימום ניסיונות — נחסם"""
        await store_otp(user_id=888, otp="123456")
        # 5 ניסיונות שגויים
        for _ in range(5):
            await verify_otp(888, "000000")
        # הניסיון ה-6 נכשל גם עם הקוד הנכון
        result = await verify_otp(888, "123456")
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_verify_resets_attempts(self):
        """אימות מוצלח מאפס את מונה הניסיונות"""
        await store_otp(user_id=777, otp="123456")
        # ניסיונות שגויים
        await verify_otp(777, "000000")
        await verify_otp(777, "000000")
        # אימות נכון
        result = await verify_otp(777, "123456")
        assert result is True

    @pytest.mark.asyncio
    async def test_new_otp_request_resets_attempts(self):
        """בקשת OTP חדש מאפסת מונה ניסיונות — תיקון באג חסימה קבועה"""
        await store_otp(user_id=666, otp="111111")
        # 5 ניסיונות שגויים — נחסם
        for _ in range(5):
            await verify_otp(666, "000000")
        result_blocked = await verify_otp(666, "111111")
        assert result_blocked is False  # נחסם

        # בקשת OTP חדש — חייבת לאפס מונה
        await store_otp(user_id=666, otp="222222")
        result_after_reset = await verify_otp(666, "222222")
        assert result_after_reset is True  # עובד שוב

    @pytest.mark.asyncio
    async def test_non_consuming_verify_does_not_waste_attempts(self):
        """אימות עם consume=False לא מבזבז ניסיונות — station picker לא שורף מכסה"""
        await store_otp(user_id=555, otp="123456")
        # 4 אימותים מוצלחים ללא צריכה (כמו station picker retries)
        for _ in range(4):
            result = await verify_otp(555, "123456", consume=False)
            assert result is True
        # האימות ה-5 עם צריכה עדיין צריך להצליח — המונה לא עלה
        result = await verify_otp(555, "123456", consume=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_wrong_otp_still_counted_with_consume_false(self):
        """OTP שגוי עדיין נספר גם כש-consume=False — הגנה מ-brute force"""
        await store_otp(user_id=444, otp="123456")
        # 5 ניסיונות שגויים (consume=False לא עוזר לתוקף)
        for _ in range(5):
            await verify_otp(444, "000000", consume=False)
        # הניסיון ה-6 נכשל גם עם הקוד הנכון
        result = await verify_otp(444, "123456")
        assert result is False


class TestStationOwnerWithoutStations:
    """בדיקות שבעל תחנה ללא תחנות מקבל תשובה גנרית — מניעת user-enumeration"""

    @pytest.mark.asyncio
    async def test_owner_without_station_gets_generic_401(
        self, test_client, user_factory, db_session,
    ):
        """בעל תחנה ללא תחנה פעילה מקבל 401 גנרי — לא 403 שחושף מידע"""
        user = await user_factory(
            phone_number="+972505555555",
            role=UserRole.STATION_OWNER,
        )
        # לא יוצרים תחנה — המשתמש הוא STATION_OWNER בלי תחנה
        await store_otp(user.id, "123456")

        response = await test_client.post("/api/panel/auth/verify-otp", json={
            "phone_number": "0505555555",
            "otp": "123456",
        })
        # חייב להיות 401 (אותו דבר כמו OTP שגוי) — לא 403
        assert response.status_code == 401
        assert response.json()["detail"] == "קוד שגוי או פג תוקף"
