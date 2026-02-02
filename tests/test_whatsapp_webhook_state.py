"""
Tests for WhatsApp webhook conversation state persistence.

Regression: after refactors, WhatsApp sender identifier could change between messages
(@lid vs @c.us), causing the bot to "repeat the same question" because the session
was created for a different user record each time.
"""

import pytest
from httpx import AsyncClient


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

