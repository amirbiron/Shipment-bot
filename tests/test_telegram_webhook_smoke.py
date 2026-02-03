import pytest
from sqlalchemy import select

from app.db.models.user import User


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

