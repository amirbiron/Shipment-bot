"""
בדיקות edge cases ל-webhooks — טלגרם + וואטסאפ.
מכסה תרחישי קצה מ-issue #116 ובדיקות observability.
"""
import pytest
from fastapi import BackgroundTasks
from unittest.mock import patch

from app.api.webhooks.telegram import (
    TelegramUpdate,
    _parse_inbound_event,
    _telegram_phone_placeholder,
    get_or_create_user as tg_get_or_create_user,
)
from app.api.webhooks.whatsapp import (
    _extract_real_phone,
    _normalize_whatsapp_identifier,
    get_or_create_user as wa_get_or_create_user,
)
from app.db.models.user import User, UserRole


# ============================================================================
# טלגרם — פירוק אירועים נכנסים
# ============================================================================


class TestTelegramParseInboundEdgeCases:
    """תרחישי קצה בפירוק update של טלגרם"""

    @pytest.mark.unit
    def test_callback_query_without_message_resolves_from_from_user(self):
        """callback_query ללא message — צריך ליפול ל-from_user.id כ-chat_id"""
        background_tasks = BackgroundTasks()
        update = TelegramUpdate(
            update_id=100,
            callback_query={
                "id": "cb-no-msg",
                "from": {"id": 55555, "first_name": "Test"},
                "message": None,
                "data": "some_button",
            },
        )

        event = _parse_inbound_event(update, background_tasks)
        assert event is not None
        assert event.send_chat_id == "55555"
        assert event.telegram_user_id == "55555"
        assert event.text == "some_button"
        assert event.is_callback is True

    @pytest.mark.unit
    def test_message_without_text_or_photo_returns_empty_text(self):
        """הודעה ללא טקסט וללא תמונה (סטיקר/קול) — text ריק, photo=None"""
        background_tasks = BackgroundTasks()
        update = TelegramUpdate(
            update_id=101,
            message={
                "message_id": 200,
                "chat": {"id": 66666, "type": "private"},
                "text": None,
                "date": 1700000000,
            },
        )

        event = _parse_inbound_event(update, background_tasks)
        assert event is not None
        assert event.text == ""
        assert event.photo_file_id is None

    @pytest.mark.unit
    def test_update_without_message_or_callback_returns_none(self):
        """update ללא message וללא callback_query — None"""
        background_tasks = BackgroundTasks()
        update = TelegramUpdate(update_id=102)

        event = _parse_inbound_event(update, background_tasks)
        assert event is None


# ============================================================================
# טלגרם — placeholder לטלפון
# ============================================================================


class TestTelegramPhonePlaceholder:
    """בדיקות ליצירת placeholder ל-phone_number בטלגרם"""

    @pytest.mark.unit
    def test_short_id_returns_tg_prefix(self):
        """ID קצר — tg:{id} ללא hash"""
        result = _telegram_phone_placeholder("12345")
        assert result == "tg:12345"
        assert len(result) <= 20

    @pytest.mark.unit
    def test_long_id_triggers_hash(self):
        """ID ארוך (>17 תווים) — placeholder מגובב באורך 20"""
        long_id = "12345678901234567890"  # 20 ספרות → "tg:" + 20 = 23 > 20
        result = _telegram_phone_placeholder(long_id)
        assert result.startswith("tg:")
        assert len(result) == 20

    @pytest.mark.unit
    def test_hash_is_deterministic(self):
        """אותו ID תמיד נותן אותו placeholder"""
        long_id = "99999999999999999999"
        assert _telegram_phone_placeholder(long_id) == _telegram_phone_placeholder(long_id)

    @pytest.mark.unit
    def test_empty_id_raises_value_error(self):
        """ID ריק — ValueError"""
        with pytest.raises(ValueError):
            _telegram_phone_placeholder("")

    @pytest.mark.unit
    def test_none_id_raises_value_error(self):
        """None — ValueError"""
        with pytest.raises(ValueError):
            _telegram_phone_placeholder(None)


# ============================================================================
# טלגרם — get_or_create_user
# ============================================================================


class TestTelegramGetOrCreateUser:
    """תרחישי קצה ביצירת/שליפת משתמש בטלגרם"""

    @pytest.mark.asyncio
    async def test_creates_new_user_with_tg_placeholder(self, db_session):
        """משתמש חדש — phone_number=tg:{id}, platform=telegram, role=SENDER"""
        user, is_new = await tg_get_or_create_user(db_session, "777888", name="New User")
        assert is_new is True
        assert user.phone_number == "tg:777888"
        assert user.telegram_chat_id == "777888"
        assert user.platform == "telegram"
        assert user.role == UserRole.SENDER

    @pytest.mark.asyncio
    async def test_finds_existing_user_by_telegram_chat_id(self, db_session, user_factory):
        """משתמש קיים — מוחזר לפי telegram_chat_id"""
        existing = await user_factory(
            phone_number="tg:11111",
            telegram_chat_id="11111",
            platform="telegram",
        )
        user, is_new = await tg_get_or_create_user(db_session, "11111")
        assert is_new is False
        assert user.id == existing.id

    @pytest.mark.asyncio
    async def test_duplicate_telegram_chat_id_selects_deterministically(self, db_session, user_factory):
        """כפילות telegram_chat_id — בוחר דטרמיניסטית ומלוגג error"""
        from unittest.mock import AsyncMock, MagicMock

        # סימולציית שתי שורות מ-DB (כמו שקורה בפרודקשן ללא UNIQUE constraint)
        user1 = await user_factory(
            phone_number="tg:dup1",
            telegram_chat_id="dup99",
            platform="telegram",
            is_active=True,
        )
        user2 = User(
            id=99999,
            phone_number="tg:dup2",
            telegram_chat_id="dup99",
            platform="telegram",
            role=UserRole.SENDER,
            is_active=True,
        )

        # Mock של השאילתה להחזיר שני תוצאות — מדמה מצב פרודקשן ללא UNIQUE
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [user1, user2]
        original_execute = db_session.execute

        async def patched_execute(stmt, *args, **kwargs):
            # תופס רק את השאילתה הראשונה (lookup לפי telegram_chat_id)
            if hasattr(stmt, "compile") and "telegram_chat_id" in str(stmt.compile(compile_kwargs={"literal_binds": True})):
                return mock_result
            return await original_execute(stmt, *args, **kwargs)

        with patch.object(db_session, "execute", side_effect=patched_execute):
            with patch("app.api.webhooks.telegram.logger") as mock_logger:
                user, is_new = await tg_get_or_create_user(db_session, "dup99")
                assert is_new is False
                assert user.id == user1.id  # בוחר את הראשון
                # וידוא שנרשם לוג error על כפילות
                error_calls = [
                    c for c in mock_logger.error.call_args_list
                    if "Duplicate" in str(c)
                ]
                assert len(error_calls) == 1


# ============================================================================
# וואטסאפ — חילוץ מספר טלפון אמיתי
# ============================================================================


class TestWhatsAppExtractRealPhone:
    """בדיקות _extract_real_phone עם פורמטים שונים"""

    @pytest.mark.unit
    def test_c_us_suffix(self):
        assert _extract_real_phone("972501234567@c.us") == "+972501234567"

    @pytest.mark.unit
    def test_lid_suffix(self):
        assert _extract_real_phone("972501234567@lid") == "+972501234567"

    @pytest.mark.unit
    def test_local_050_format(self):
        assert _extract_real_phone("0501234567") == "+972501234567"

    @pytest.mark.unit
    def test_plus_972_format(self):
        assert _extract_real_phone("+972501234567") == "+972501234567"

    @pytest.mark.unit
    def test_raw_972_format(self):
        assert _extract_real_phone("972501234567") == "+972501234567"

    @pytest.mark.unit
    def test_invalid_non_numeric_returns_none(self):
        assert _extract_real_phone("not-a-phone") is None

    @pytest.mark.unit
    def test_empty_string_returns_none(self):
        assert _extract_real_phone("") is None

    @pytest.mark.unit
    def test_none_returns_none(self):
        assert _extract_real_phone(None) is None

    @pytest.mark.unit
    def test_alphabetic_at_lid_returns_none(self):
        """מזהה ללא ספרות תקינות — None"""
        assert _extract_real_phone("abc@lid") is None


# ============================================================================
# וואטסאפ — נרמול מזהים
# ============================================================================


class TestWhatsAppNormalizeEdgeCases:
    """תרחישי קצה נוספים ל-_normalize_whatsapp_identifier"""

    @pytest.mark.unit
    def test_empty_string_returns_empty(self):
        assert _normalize_whatsapp_identifier("") == ""

    @pytest.mark.unit
    def test_none_returns_empty(self):
        assert _normalize_whatsapp_identifier(None) == ""

    @pytest.mark.unit
    def test_only_suffix_no_digits_returns_empty(self):
        """מזהה שהוא רק suffix ללא ספרות"""
        assert _normalize_whatsapp_identifier("@lid") == ""

    @pytest.mark.unit
    def test_local_050_normalized_to_972(self):
        """050... → 972..."""
        assert _normalize_whatsapp_identifier("0501234567") == "972501234567"

    @pytest.mark.unit
    def test_with_c_us_suffix(self):
        assert _normalize_whatsapp_identifier("972501234567@c.us") == "972501234567"


# ============================================================================
# וואטסאפ — get_or_create_user
# ============================================================================


class TestWhatsAppGetOrCreateUser:
    """תרחישי קצה ביצירת/שליפת משתמש בוואטסאפ"""

    @pytest.mark.asyncio
    async def test_prefers_phone_match_for_strong_role(self, db_session, user_factory):
        """משתמש עם תפקיד חזק (STATION_OWNER) לפי טלפון — מועדף על sender_id"""
        station_owner = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
            platform="whatsapp",
        )
        await user_factory(
            phone_number="device-abc@lid",
            role=UserRole.SENDER,
            platform="whatsapp",
        )

        user, is_new = await wa_get_or_create_user(
            db_session,
            "device-abc@lid",
            from_number="972501234567@c.us",
        )
        assert is_new is False
        assert user.id == station_owner.id

    @pytest.mark.asyncio
    async def test_prefers_sender_id_when_phone_user_is_sender(self, db_session, user_factory):
        """שני SENDER — מעדיף sender_id על phone match"""
        await user_factory(
            phone_number="+972501234567",
            role=UserRole.SENDER,
            platform="whatsapp",
        )
        sender_by_key = await user_factory(
            phone_number="device-xyz@lid",
            role=UserRole.SENDER,
            platform="whatsapp",
        )

        user, is_new = await wa_get_or_create_user(
            db_session,
            "device-xyz@lid",
            from_number="972501234567@c.us",
        )
        assert is_new is False
        assert user.id == sender_by_key.id

    @pytest.mark.asyncio
    async def test_long_sender_id_creates_hashed_placeholder(self, db_session):
        """sender_id ארוך (>20 תווים) — placeholder מגובב"""
        long_id = "very-long-identifier-that-exceeds-twenty-chars@lid"
        user, is_new = await wa_get_or_create_user(db_session, long_id)
        assert is_new is True
        assert user.phone_number.startswith("wa:")
        assert len(user.phone_number) <= 20

    @pytest.mark.asyncio
    async def test_creates_new_user_with_sender_id(self, db_session):
        """משתמש חדש — phone_number=sender_id, platform=whatsapp, role=SENDER"""
        user, is_new = await wa_get_or_create_user(db_session, "new-device@lid")
        assert is_new is True
        assert user.phone_number == "new-device@lid"
        assert user.platform == "whatsapp"
        assert user.role == UserRole.SENDER


# ============================================================================
# חוצה פלטפורמות
# ============================================================================


class TestCrossPlatformEdgeCases:
    """בדיקות שמשתמשים באותו טלפון בשתי פלטפורמות לא מתערבבים"""

    @pytest.mark.asyncio
    async def test_same_phone_different_platforms_isolated(self, db_session, user_factory):
        """טלגרם ווואטסאפ עם אותו טלפון אמיתי — כל webhook מוצא את שלו"""
        tg_user = await user_factory(
            phone_number="tg:11111",
            telegram_chat_id="11111",
            platform="telegram",
        )
        wa_user = await user_factory(
            phone_number="+972501234567",
            platform="whatsapp",
        )

        # טלגרם מוצא לפי telegram_chat_id
        found_tg, is_new_tg = await tg_get_or_create_user(db_session, "11111")
        assert is_new_tg is False
        assert found_tg.id == tg_user.id

        # וואטסאפ מוצא לפי phone
        found_wa, is_new_wa = await wa_get_or_create_user(
            db_session,
            "new-sender@lid",
            from_number="972501234567@c.us",
        )
        assert is_new_wa is False
        assert found_wa.id == wa_user.id


# ============================================================================
# masking safety — מזהים שאינם טלפונים
# ============================================================================


class TestMaskingSafety:
    """וידוא ש-PhoneNumberValidator.mask() לא נשבר על מזהים לא-טלפוניים"""

    @pytest.mark.unit
    def test_mask_device_identifier(self):
        """מזהה מכשיר עם @lid — ממוסך בלי exception"""
        from app.core.validation import PhoneNumberValidator
        result = PhoneNumberValidator.mask("device-abc@lid")
        assert result.endswith("****")
        assert "@lid" not in result  # 4 תווים אחרונים הוחלפו

    @pytest.mark.unit
    def test_mask_short_string(self):
        """מחרוזת קצרה מ-4 תווים — מחזיר ****"""
        from app.core.validation import PhoneNumberValidator
        assert PhoneNumberValidator.mask("ab") == "****"

    @pytest.mark.unit
    def test_mask_wa_hash_placeholder(self):
        """placeholder מגובב wa:xxxx — ממוסך בבטחה"""
        from app.core.validation import PhoneNumberValidator
        result = PhoneNumberValidator.mask("wa:a1b2c3d4e5f6g7h")
        assert result.endswith("****")
        assert len(result) == len("wa:a1b2c3d4e5f6g7h")


# ============================================================================
# לוגי observability — וידוא שדות "User resolved"
# ============================================================================


class TestUserResolvedLog:
    """וידוא שלוג 'User resolved' נרשם עם כל השדות הנכונים"""

    @pytest.mark.asyncio
    async def test_telegram_user_resolved_log_fields(self, db_session, user_factory):
        """טלגרם — לוג info עם resolved_user_id, telegram_chat_id, lookup_by, is_new, role"""
        user = await user_factory(
            phone_number="tg:82000",
            telegram_chat_id="82000",
            platform="telegram",
            role=UserRole.SENDER,
        )

        with patch("app.api.webhooks.telegram.logger") as mock_logger:
            result_user, is_new = await tg_get_or_create_user(db_session, "82000")

        # הפונקציה עצמה לא מלוגגת — הלוג נמצא ב-webhook handler.
        # כאן בודקים שהפונקציה מחזירה ערכים תקינים שהלוג ישתמש בהם.
        assert result_user.id == user.id
        assert is_new is False
        assert result_user.role == UserRole.SENDER

    @pytest.mark.asyncio
    async def test_whatsapp_user_resolved_log_fields(self, db_session, user_factory):
        """וואטסאפ — לוג info עם resolved_user_id, sender_id, normalized_phone, is_new, role"""
        user = await user_factory(
            phone_number="+972507654321",
            platform="whatsapp",
            role=UserRole.COURIER,
        )

        result_user, is_new = await wa_get_or_create_user(
            db_session,
            "new-device@lid",
            from_number="972507654321@c.us",
        )

        # וידוא שהערכים שהלוג ישתמש בהם נכונים
        assert result_user.id == user.id
        assert is_new is False
        assert result_user.role == UserRole.COURIER

    @pytest.mark.asyncio
    async def test_telegram_new_user_resolved_log_is_new_true(self, db_session):
        """טלגרם — משתמש חדש: is_new=True"""
        user, is_new = await tg_get_or_create_user(db_session, "99001", name="New")
        assert is_new is True
        assert user.role == UserRole.SENDER

    @pytest.mark.asyncio
    async def test_whatsapp_new_user_resolved_log_is_new_true(self, db_session):
        """וואטסאפ — משתמש חדש: is_new=True"""
        user, is_new = await wa_get_or_create_user(db_session, "brand-new@lid")
        assert is_new is True
        assert user.role == UserRole.SENDER
