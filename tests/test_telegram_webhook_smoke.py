import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.db.models.user import User, UserRole, ApprovalStatus
from app.core.config import settings


@pytest.mark.asyncio
async def test_telegram_webhook_creates_user_with_phone_placeholder(test_client, db_session):
    # New telegram user
    resp = await test_client.post(
        "/api/telegram/webhook",
        json={
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 12345, "type": "private"},
                "text": "תפריט",
                "date": 1700000000,
                "from": {"id": 999, "first_name": "Smoke"},
            },
        },
    )
    assert resp.status_code == 200

    result = await db_session.execute(select(User).where(User.telegram_chat_id == "12345"))
    user = result.scalar_one()
    assert user.phone_number is not None
    assert user.phone_number.startswith("tg:")


@pytest.mark.asyncio
async def test_telegram_callback_query_without_message_uses_from_user_id(test_client, db_session):
    resp = await test_client.post(
        "/api/telegram/webhook",
        json={
            "update_id": 2,
            "callback_query": {
                "id": "cb-1",
                "data": "תפריט",
                "from": {"id": 777, "first_name": "Cb"},
                "message": None,
            },
        },
    )
    assert resp.status_code == 200

    result = await db_session.execute(select(User).where(User.telegram_chat_id == "777"))
    user = result.scalar_one()
    assert user.phone_number is not None
    assert user.phone_number != "tg:None"
    assert user.phone_number.startswith("tg:")


@pytest.mark.asyncio
async def test_telegram_document_image_is_captured_as_photo(test_client, db_session, user_factory):
    """קובץ תמונה שנשלח כמסמך (לא כתמונה דחוסה) נתפס כ-photo_file_id"""
    # יצירת שליח באמצע רישום - ממתין למסמך זהות
    courier = await user_factory(
        phone_number="tg:88801",
        name="Doc Test",
        role=UserRole.COURIER,
        platform="telegram",
        telegram_chat_id="88801",
    )
    from app.state_machine.manager import StateManager
    from app.state_machine.states import CourierState
    sm = StateManager(db_session)
    await sm.force_state(courier.id, "telegram", CourierState.REGISTER_COLLECT_DOCUMENT.value, context={})

    with patch.object(settings, "TELEGRAM_BOT_TOKEN", "fake-token"):
        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 3,
                "message": {
                    "message_id": 3,
                    "chat": {"id": 88801, "type": "private"},
                    "from": {"id": 88801, "first_name": "DocTest"},
                    "date": 1700000000,
                    # מסמך תמונה (כמו שליחה מדסקטופ "שלח כקובץ")
                    "document": {
                        "file_id": "BQACAgIAAx_doc_image_123",
                        "file_unique_id": "unique_doc_123",
                        "file_name": "id_card.jpg",
                        "mime_type": "image/jpeg",
                    },
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # המסמך צריך להתקבל - ולא להיתקע ב-REGISTER_COLLECT_DOCUMENT
        assert data.get("new_state") == CourierState.REGISTER_COLLECT_SELFIE.value

    # אימות שה-file_id נשמר בDB
    await db_session.refresh(courier)
    assert courier.id_document_url == "BQACAgIAAx_doc_image_123"


@pytest.mark.asyncio
async def test_telegram_non_image_document_is_ignored(test_client, db_session, user_factory):
    """קובץ שאינו תמונה (כמו PDF) לא נתפס כ-photo_file_id"""
    courier = await user_factory(
        phone_number="tg:88802",
        name="PDF Test",
        role=UserRole.COURIER,
        platform="telegram",
        telegram_chat_id="88802",
    )
    from app.state_machine.manager import StateManager
    from app.state_machine.states import CourierState
    sm = StateManager(db_session)
    await sm.force_state(courier.id, "telegram", CourierState.REGISTER_COLLECT_DOCUMENT.value, context={})

    with patch.object(settings, "TELEGRAM_BOT_TOKEN", "fake-token"):
        resp = await test_client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 4,
                "message": {
                    "message_id": 4,
                    "chat": {"id": 88802, "type": "private"},
                    "from": {"id": 88802, "first_name": "PdfTest"},
                    "date": 1700000000,
                    # מסמך PDF - לא תמונה
                    "document": {
                        "file_id": "BQACAgIAAx_pdf_123",
                        "file_unique_id": "unique_pdf_123",
                        "file_name": "document.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # PDF לא נתפס כתמונה - אין text ואין photo אז ההודעה נדלגת
        assert "new_state" not in data or data.get("new_state") != CourierState.REGISTER_COLLECT_SELFIE.value

