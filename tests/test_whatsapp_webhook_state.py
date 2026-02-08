"""
Tests for WhatsApp webhook conversation state persistence.

Regression: after refactors, WhatsApp sender identifier could change between messages
(@lid vs @c.us), causing the bot to "repeat the same question" because the session
was created for a different user record each time.
"""

import pytest
from unittest.mock import patch
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.user import User, UserRole, ApprovalStatus
from app.state_machine.states import CourierState
from app.core.config import settings


@pytest.mark.integration
async def test_whatsapp_state_persists_across_reply_to_changes(
    test_client: AsyncClient,
    mock_whatsapp_gateway,
):
    sender_id = "123456@lid"  # stable chat identifier

    async def post(text: str, reply_to: str) -> dict:
        payload = {
            "messages": [
                {
                    "from_number": reply_to,  # legacy field (can change)
                    "sender_id": sender_id,  # stable field (must not change)
                    "reply_to": reply_to,  # where to actually reply
                    "message_id": "m1",
                    "text": text,
                    "timestamp": 1700000000,
                }
            ]
        }
        r = await test_client.post("/api/webhooks/whatsapp/webhook", json=payload)
        assert r.status_code == 200
        return r.json()

    # 1) First message creates user (welcome)
    res1 = await post("שלום", reply_to="972501234567@c.us")
    assert res1["processed"] == 1
    assert res1["responses"][0]["new_user"] is True

    # 2) Start sender flow (should ask for name)
    res2 = await post("📦 אני רוצה לשלוח חבילה", reply_to="972501234567@c.us")
    assert res2["responses"][0]["new_state"] == "SENDER.REGISTER.COLLECT_NAME"

    # 3) Provide name, but change reply_to to simulate gateway identifier changes
    res3 = await post("Test User", reply_to="972501234567@lid")
    assert res3["responses"][0]["new_state"] == "SENDER.MENU"

    # 4) Choose "new delivery" with reply_to changed again
    res4 = await post("➕ משלוח חדש", reply_to="972501234567@c.us")
    assert res4["responses"][0]["new_state"] == "SENDER.DELIVERY.PICKUP_CITY"

    # 5) Provide city; bot must advance to street, not repeat the city question
    res5 = await post("תל אביב", reply_to="972501234567@lid")
    assert res5["responses"][0]["new_state"] == "SENDER.DELIVERY.PICKUP_STREET"


@pytest.mark.asyncio
async def test_whatsapp_document_image_captured_as_photo(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
):
    """מסמך תמונה (media_type=document + mime_type=image/jpeg) נתפס כ-photo בוואטסאפ"""
    courier = await user_factory(
        phone_number="972551234@lid",
        name="DocTest WA",
        role=UserRole.COURIER,
        platform="whatsapp",
    )
    from app.state_machine.manager import StateManager
    sm = StateManager(db_session)
    await sm.force_state(courier.id, "whatsapp", CourierState.REGISTER_COLLECT_DOCUMENT.value, context={})

    resp = await test_client.post(
        "/api/webhooks/whatsapp/webhook",
        json={
            "messages": [{
                "from_number": "972551234@lid",
                "sender_id": "972551234@lid",
                "message_id": "m-doc-1",
                "text": "",
                "timestamp": 1700000000,
                "media_url": "http://gateway/media/id_card.jpg",
                "media_type": "document",
                "mime_type": "image/jpeg",
            }]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # מסמך תמונה צריך להתקבל - ולהעביר למצב סלפי
    assert data["responses"][0]["new_state"] == CourierState.REGISTER_COLLECT_SELFIE.value

    # אימות שה-URL נשמר
    await db_session.refresh(courier)
    assert courier.id_document_url == "http://gateway/media/id_card.jpg"


@pytest.mark.asyncio
async def test_whatsapp_non_image_document_not_captured(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
):
    """מסמך PDF (media_type=document + mime_type=application/pdf) לא נתפס כ-photo"""
    courier = await user_factory(
        phone_number="972551235@lid",
        name="PdfTest WA",
        role=UserRole.COURIER,
        platform="whatsapp",
    )
    from app.state_machine.manager import StateManager
    sm = StateManager(db_session)
    await sm.force_state(courier.id, "whatsapp", CourierState.REGISTER_COLLECT_DOCUMENT.value, context={})

    resp = await test_client.post(
        "/api/webhooks/whatsapp/webhook",
        json={
            "messages": [{
                "from_number": "972551235@lid",
                "sender_id": "972551235@lid",
                "message_id": "m-pdf-1",
                "text": "",
                "timestamp": 1700000000,
                "media_url": "http://gateway/media/doc.pdf",
                "media_type": "document",
                "mime_type": "application/pdf",
            }]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # PDF לא נתפס כתמונה - ההודעה נדלגת (אין text ואין photo)
    assert data["processed"] == 0 or (
        "new_state" not in data.get("responses", [{}])[0]
        or data["responses"][0].get("new_state") != CourierState.REGISTER_COLLECT_SELFIE.value
    )

