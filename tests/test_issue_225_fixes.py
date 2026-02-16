"""
בדיקות לתיקוני issue #225 — כשלים.

1. race condition ב-get_or_create_user + ניקוי כפילויות telegram_chat_id
2. HTML escaping בהודעות Telegram של admin_notification_service
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.core.config import settings
from app.core.validation import TextSanitizer
from app.db.models.user import User, UserRole
from app.domain.services.admin_notification_service import AdminNotificationService


# ============================================================================
# get_or_create_user — ניקוי כפילויות ו-race condition
# ============================================================================


@pytest.mark.asyncio
async def test_get_or_create_user_deactivates_duplicates(db_session, user_factory):
    """כשיש כפילויות telegram_chat_id — משביתה את הרשומות הכפולות ושומרת את הפעילה"""
    from app.api.webhooks.telegram import get_or_create_user

    # יצירת 2 משתמשים עם telegram_chat_id שונה — נדמה כפילות ע"י mock
    user1 = await user_factory(
        phone_number="+972501110001",
        name="User Primary",
        telegram_chat_id="dup_chat_1",
        platform="telegram",
        is_active=True,
    )
    user2 = await user_factory(
        phone_number="+972501110002",
        name="User Duplicate",
        telegram_chat_id="dup_chat_2",
        platform="telegram",
        is_active=True,
    )

    # מדמים תוצאת שאילתה שמחזירה 2 משתמשים (כאילו telegram_chat_id כפול)
    from unittest.mock import MagicMock
    original_execute = db_session.execute

    first_query = True

    async def _mock_execute(stmt, *args, **kwargs):
        nonlocal first_query
        result = await original_execute(stmt, *args, **kwargs)
        # רק בשאילתה הראשונה (חיפוש לפי telegram_chat_id) — מחזירים 2 משתמשים
        if first_query and hasattr(stmt, 'whereclause') and stmt.whereclause is not None:
            first_query = False
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [user1, user2]
            return mock_result
        return result

    with patch.object(db_session, "execute", side_effect=_mock_execute):
        user, is_new = await get_or_create_user(db_session, "dup_chat_1")

    assert is_new is False
    assert user.id == user1.id  # שומרת את הראשונה

    # בודקים שהכפילות הושבתה
    assert user2.is_active is False
    assert user2.telegram_chat_id is None


@pytest.mark.asyncio
async def test_get_or_create_user_creates_new_user(db_session):
    """משתמש חדש — נוצר בהצלחה"""
    from app.api.webhooks.telegram import get_or_create_user

    user, is_new = await get_or_create_user(db_session, "new_chat_999", name="חדש")

    assert is_new is True
    assert user.telegram_chat_id == "new_chat_999"
    assert user.name == "חדש"
    assert user.platform == "telegram"
    assert user.role == UserRole.SENDER


@pytest.mark.asyncio
async def test_get_or_create_user_returns_existing_user(db_session, user_factory):
    """משתמש קיים — מוחזר ללא יצירה חדשה"""
    from app.api.webhooks.telegram import get_or_create_user

    existing = await user_factory(
        phone_number="+972501110003",
        name="Existing",
        telegram_chat_id="existing_chat",
        platform="telegram",
    )

    user, is_new = await get_or_create_user(db_session, "existing_chat")

    assert is_new is False
    assert user.id == existing.id


@pytest.mark.asyncio
async def test_get_or_create_user_handles_integrity_error(db_session, user_factory):
    """race condition — IntegrityError נתפס ומוחזר המשתמש שנוצר ע"י הבקשה המקבילית"""
    from sqlalchemy.exc import IntegrityError
    from app.api.webhooks.telegram import get_or_create_user

    original_commit = db_session.commit
    original_rollback = db_session.rollback

    # יצירת המשתמש ש"בקשה מקבילית" כבר יצרה
    existing = await user_factory(
        phone_number="+972501110099",
        name="Race Winner",
        telegram_chat_id="race_chat",
        platform="telegram",
    )

    # מחיקת המשתמש מ-identity map כדי שה-select הראשון לא ימצא אותו
    db_session.expunge(existing)

    first_commit = True

    async def _commit_simulating_race():
        nonlocal first_commit
        if first_commit:
            first_commit = False
            raise IntegrityError("mock duplicate", {}, Exception("UNIQUE constraint"))
        await original_commit()

    async def _rollback_clean():
        await original_rollback()

    with patch.object(db_session, "commit", side_effect=_commit_simulating_race):
        with patch.object(db_session, "rollback", side_effect=_rollback_clean):
            # קריאה עם telegram_chat_id שנראה חדש אך ה-commit יכשל
            # (מדמה מצב שהשאילתה הראשונה לא מצאה, אך בינתיים בקשה אחרת יצרה)
            user, is_new = await get_or_create_user(db_session, "race_chat")

    assert is_new is False
    assert user.telegram_chat_id == "race_chat"


# ============================================================================
# HTML escaping בהודעות Telegram
# ============================================================================


@pytest.mark.unit
async def test_notification_html_escapes_user_name(monkeypatch):
    """שם משתמש עם תווי HTML — נשלח escaped כדי למנוע 400 מטלגרם"""
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_GROUP_ID", None)
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", "admin-chat")

    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        AdminNotificationService,
        "_send_telegram_message_with_inline_keyboard",
        send_mock,
    )

    ok = await AdminNotificationService.notify_new_courier_registration(
        user_id=100,
        full_name="<script>alert(1)</script>",
        service_area="תל אביב & סביבה",
        phone_or_chat_id="555",
        platform="telegram",
    )

    assert ok is True
    sent_msg = send_mock.call_args[0][1]
    # HTML entities — התווים המסוכנים הומרו
    assert "&lt;script&gt;" in sent_msg
    assert "<script>" not in sent_msg
    assert "&amp;" in sent_msg


@pytest.mark.unit
async def test_group_decision_html_escapes_fields(monkeypatch):
    """notify_group_courier_decision — escaped HTML בשדות קלט משתמש"""
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_GROUP_ID", None)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "group-chat")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")

    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        AdminNotificationService,
        "_send_telegram_message",
        send_mock,
    )

    ok = await AdminNotificationService.notify_group_courier_decision(
        user_id=200,
        full_name="<b>Hacker</b>",
        service_area="ירושלים",
        vehicle_category="car_4",
        platform="telegram",
        decision="approved",
        decided_by="Admin <O'Brien>",
    )

    assert ok is True
    sent_msg = send_mock.call_args[0][1]
    # full_name escaped
    assert "&lt;b&gt;Hacker&lt;/b&gt;" in sent_msg
    # decided_by escaped
    assert "Admin &lt;O&#x27;Brien&gt;" in sent_msg or "Admin &lt;O&#39;Brien&gt;" in sent_msg


@pytest.mark.unit
async def test_deposit_notification_html_escapes_name(monkeypatch):
    """notify_deposit_request — שם עם תווי HTML מועבר escaped"""
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "admin-chat")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")

    with patch.object(
        AdminNotificationService,
        "_send_telegram_message",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_send, patch.object(
        AdminNotificationService,
        "_forward_photo",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await AdminNotificationService.notify_deposit_request(
            user_id=300,
            full_name="<img src=x>",
            contact_identifier="12345",
            screenshot_file_id="file_id",
            platform="telegram",
        )

    assert result is True
    sent_msg = mock_send.call_args[0][1]
    assert "&lt;img src=x&gt;" in sent_msg
    assert "<img src=x>" not in sent_msg


# ============================================================================
# TextSanitizer.sanitize_for_html — בדיקות ישירות
# ============================================================================


@pytest.mark.unit
def test_sanitize_for_html_basic():
    """תווים מסוכנים — escaped"""
    assert TextSanitizer.sanitize_for_html("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"
    assert TextSanitizer.sanitize_for_html("a & b") == "a &amp; b"


@pytest.mark.unit
def test_sanitize_for_html_empty():
    """מחרוזת ריקה — מוחזרת ריקה"""
    assert TextSanitizer.sanitize_for_html("") == ""


@pytest.mark.unit
def test_sanitize_for_html_safe_text():
    """טקסט בטוח — לא משתנה"""
    assert TextSanitizer.sanitize_for_html("שם רגיל") == "שם רגיל"
