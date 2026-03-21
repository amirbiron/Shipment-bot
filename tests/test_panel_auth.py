"""
בדיקות אימות לפאנל ווב — OTP + JWT + Telegram Login
"""
import hashlib
import hmac
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from app.core.auth import (
    create_access_token,
    generate_otp,
    store_otp,
    try_set_otp_cooldown_by_phone,
    verify_otp,
    verify_telegram_login_data,
    verify_token,
)
from app.core.config import settings
from app.db.models.user import UserRole
from app.db.models.outbox_message import OutboxMessage, MessagePlatform, MessageStatus
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


class TestOTPDelivery:
    """בדיקות שליחת OTP — dispatch מיידי וזיהוי פלטפורמה"""

    @pytest.mark.asyncio
    async def test_otp_triggers_immediate_celery_task(
        self, test_client, user_factory, db_session,
    ):
        """בקשת OTP מפעילה send_message.delay מיד — לא מחכה ל-beat"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="123456789",
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        with patch("app.workers.tasks.send_message") as mock_send:
            mock_send.delay = MagicMock()
            response = await test_client.post("/api/panel/auth/request-otp", json={
                "phone_number": "0501234567",
            })
            assert response.status_code == 200
            # ולידציה ש-send_message.delay נקרא עם message ID
            mock_send.delay.assert_called_once()
            msg_id = mock_send.delay.call_args[0][0]
            assert isinstance(msg_id, int)

    @pytest.mark.asyncio
    async def test_otp_sent_via_telegram_when_chat_id_exists(
        self, test_client, user_factory, db_session,
    ):
        """OTP נשלח דרך טלגרם כשיש telegram_chat_id"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה טלגרם",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="987654321",
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        with patch("app.workers.tasks.send_message") as mock_send:
            mock_send.delay = MagicMock()
            await test_client.post("/api/panel/auth/request-otp", json={
                "phone_number": "0501234567",
            })

        # בדיקה שההודעה ב-outbox היא טלגרם
        from sqlalchemy import select
        result = await db_session.execute(
            select(OutboxMessage).where(
                OutboxMessage.message_type == "panel_otp"
            )
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.platform == MessagePlatform.TELEGRAM
        assert msg.recipient_id == "987654321"

    @pytest.mark.asyncio
    async def test_otp_falls_back_to_whatsapp_without_chat_id(
        self, test_client, user_factory, db_session,
    ):
        """OTP נופל ל-WhatsApp כשאין telegram_chat_id — עם warning בלוג"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה בלי צ'אט",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id=None,  # אין chat_id
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        with patch("app.workers.tasks.send_message") as mock_send:
            mock_send.delay = MagicMock()
            await test_client.post("/api/panel/auth/request-otp", json={
                "phone_number": "0501234567",
            })

        # בדיקה שההודעה ב-outbox היא WhatsApp (fallback)
        from sqlalchemy import select
        result = await db_session.execute(
            select(OutboxMessage).where(
                OutboxMessage.message_type == "panel_otp"
            )
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.platform == MessagePlatform.WHATSAPP
        assert msg.recipient_id == "+972501234567"

    @pytest.mark.asyncio
    async def test_redis_failure_prevents_outbox_commit(
        self, test_client, user_factory, db_session,
    ):
        """כשל ב-store_otp (Redis) מונע commit של הודעת outbox — לא נשלח קוד שאי אפשר לאמת"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה Redis כשל",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="111222333",
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        with (
            patch("app.workers.tasks.send_message") as mock_send,
            patch(
                "app.api.routes.panel.auth.store_otp",
                new_callable=AsyncMock,
                side_effect=ConnectionError("Redis connection refused"),
            ),
        ):
            mock_send.delay = MagicMock()
            response = await test_client.post("/api/panel/auth/request-otp", json={
                "phone_number": "0501234567",
            })

            # תגובה צריכה להיות כישלון
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False

            # send_message לא נקרא
            mock_send.delay.assert_not_called()

        # אין הודעת outbox ב-DB — לא committed
        from sqlalchemy import select
        result = await db_session.execute(
            select(OutboxMessage).where(
                OutboxMessage.message_type == "panel_otp"
            )
        )
        msg = result.scalar_one_or_none()
        assert msg is None, "הודעת outbox נשמרה למרות כשל Redis — OTP לא ניתן לאימות"

    @pytest.mark.asyncio
    async def test_redis_cooldown_failure_returns_error_not_500(
        self, test_client, user_factory, db_session,
    ):
        """כשל Redis בבדיקת cooldown מחזיר שגיאה ידידותית — לא 500 גנרי"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה cooldown כשל",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="444555666",
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        with patch(
            "app.api.routes.panel.auth.try_set_otp_cooldown_by_phone",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Redis connection refused"),
        ):
            response = await test_client.post("/api/panel/auth/request-otp", json={
                "phone_number": "0501234567",
            })

            # לא 500 — שגיאה ידידותית
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False
            assert "שגיאה" in data["message"]


# ==================== Telegram Login ====================


_TEST_BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _make_telegram_auth_data(
    telegram_id: int = 123456789,
    first_name: str = "Test",
    auth_date: int | None = None,
    bot_token: str = _TEST_BOT_TOKEN,
) -> dict:
    """יצירת נתוני אימות טלגרם תקפים עם hash נכון"""
    if auth_date is None:
        auth_date = int(time.time())
    data = {
        "id": telegram_id,
        "first_name": first_name,
        "auth_date": auth_date,
    }
    # חישוב hash לפי פרוטוקול טלגרם
    check_pairs = sorted(f"{k}={v}" for k, v in data.items())
    data_check_string = "\n".join(check_pairs)
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    data["hash"] = computed_hash
    return data


def _patch_bot_token():
    """mock ל-TELEGRAM_BOT_TOKEN — מחזיר patch חדש כל פעם (מונע דליפת state)"""
    return patch.object(settings, "TELEGRAM_BOT_TOKEN", _TEST_BOT_TOKEN)


class TestTelegramLoginVerification:
    """בדיקות אימות נתוני Telegram Login Widget"""

    @pytest.mark.unit
    def test_valid_telegram_auth_data(self):
        """נתוני אימות תקינים עוברים ולידציה"""
        with _patch_bot_token():
            data = _make_telegram_auth_data()
            assert verify_telegram_login_data(data) is True

    @pytest.mark.unit
    def test_invalid_hash_rejected(self):
        """hash שגוי נדחה"""
        with _patch_bot_token():
            data = _make_telegram_auth_data()
            data["hash"] = "invalid_hash"
            assert verify_telegram_login_data(data) is False

    @pytest.mark.unit
    def test_missing_hash_rejected(self):
        """נתונים ללא hash נדחים"""
        with _patch_bot_token():
            data = _make_telegram_auth_data()
            del data["hash"]
            assert verify_telegram_login_data(data) is False

    @pytest.mark.unit
    def test_expired_auth_data_rejected(self):
        """נתונים עם auth_date ישן (מעל 5 דקות) נדחים"""
        with _patch_bot_token():
            old_time = int(time.time()) - 600  # לפני 10 דקות
            data = _make_telegram_auth_data(auth_date=old_time)
            assert verify_telegram_login_data(data) is False

    @pytest.mark.unit
    def test_tampered_data_rejected(self):
        """שינוי נתונים אחרי חתימה נדחה"""
        with _patch_bot_token():
            data = _make_telegram_auth_data(first_name="Original")
            data["first_name"] = "Tampered"
            assert verify_telegram_login_data(data) is False

    @pytest.mark.unit
    def test_missing_auth_date_rejected(self):
        """נתונים ללא auth_date נדחים"""
        with _patch_bot_token():
            data = _make_telegram_auth_data()
            del data["auth_date"]
            assert verify_telegram_login_data(data) is False


class TestTelegramLoginEndpoints:
    """בדיקות API endpoints של כניסה דרך טלגרם"""

    @pytest.mark.asyncio
    async def test_telegram_bot_info_enabled(self, test_client):
        """מידע על הבוט כשטלגרם מופעל"""
        with (
            patch.object(settings, "TELEGRAM_BOT_USERNAME", "test_bot"),
            _patch_bot_token(),
        ):
            response = await test_client.get("/api/panel/auth/telegram-bot-info")
        assert response.status_code == 200
        data = response.json()
        assert data["bot_username"] == "test_bot"
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_telegram_bot_info_disabled(self, test_client):
        """מידע על הבוט כשטלגרם מושבת"""
        with patch.object(settings, "TELEGRAM_BOT_USERNAME", ""):
            response = await test_client.get("/api/panel/auth/telegram-bot-info")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_telegram_login_success(
        self, test_client, user_factory, db_session,
    ):
        """כניסה מוצלחת דרך טלגרם — מחזיר JWT"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה טלגרם",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="123456789",
        )
        station = Station(name="תחנת טלגרם", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=123456789)
            response = await test_client.post("/api/panel/auth/telegram-login", json=auth_data)
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["station_name"] == "תחנת טלגרם"

    @pytest.mark.asyncio
    async def test_telegram_login_unknown_user(self, test_client):
        """כניסה דרך טלגרם עם משתמש לא קיים — 401"""
        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=999999999)
            response = await test_client.post("/api/panel/auth/telegram-login", json=auth_data)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_telegram_login_invalid_hash(
        self, test_client, user_factory, db_session,
    ):
        """כניסה דרך טלגרם עם hash שגוי — 401"""
        await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
            telegram_chat_id="123456789",
        )

        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=123456789)
            auth_data["hash"] = "invalid"
            response = await test_client.post("/api/panel/auth/telegram-login", json=auth_data)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_telegram_login_non_owner_rejected(
        self, test_client, user_factory,
    ):
        """כניסה דרך טלגרם למשתמש שאינו בעל תחנה — 401"""
        await user_factory(
            phone_number="+972501234567",
            role=UserRole.SENDER,
            telegram_chat_id="123456789",
        )

        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=123456789)
            response = await test_client.post("/api/panel/auth/telegram-login", json=auth_data)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_telegram_login_inactive_user_rejected(
        self, test_client, user_factory, db_session,
    ):
        """כניסה דרך טלגרם למשתמש לא פעיל — 401"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
            telegram_chat_id="123456789",
            is_active=False,
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=123456789)
            response = await test_client.post("/api/panel/auth/telegram-login", json=auth_data)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_telegram_login_multiple_stations_returns_picker(
        self, test_client, user_factory, db_session,
    ):
        """כניסה דרך טלגרם עם כמה תחנות — מחזיר station picker"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל כמה תחנות",
            role=UserRole.STATION_OWNER,
            telegram_chat_id="123456789",
        )
        for name in ["תחנה א", "תחנה ב"]:
            station = Station(name=name, owner_id=user.id)
            db_session.add(station)
            await db_session.flush()
            wallet = StationWallet(station_id=station.id)
            db_session.add(wallet)
        await db_session.commit()

        with _patch_bot_token():
            auth_data = _make_telegram_auth_data(telegram_id=123456789)
            response = await test_client.post("/api/panel/auth/telegram-login", json=auth_data)
        assert response.status_code == 200
        data = response.json()
        assert data["choose_station"] is True
        assert len(data["stations"]) == 2
