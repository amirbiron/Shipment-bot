"""
Tests for WhatsApp webhook conversation state persistence.

Regression: after refactors, WhatsApp sender identifier could change between messages
(@lid vs @c.us), causing the bot to "repeat the same question" because the session
was created for a different user record each time.
"""

import pytest
from httpx import AsyncClient

from app.db.models.user import UserRole, ApprovalStatus
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
    await sm.force_state(
        courier.id, "whatsapp", CourierState.REGISTER_COLLECT_DOCUMENT.value, context={}
    )

    resp = await test_client.post(
        "/api/webhooks/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": "972551234@lid",
                    "sender_id": "972551234@lid",
                    "message_id": "m-doc-1",
                    "text": "",
                    "timestamp": 1700000000,
                    "media_url": "http://gateway/media/id_card.jpg",
                    "media_type": "document",
                    "mime_type": "image/jpeg",
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # מסמך תמונה צריך להתקבל - ולהעביר למצב סלפי
    assert (
        data["responses"][0]["new_state"] == CourierState.REGISTER_COLLECT_SELFIE.value
    )

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
    await sm.force_state(
        courier.id, "whatsapp", CourierState.REGISTER_COLLECT_DOCUMENT.value, context={}
    )

    resp = await test_client.post(
        "/api/webhooks/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": "972551235@lid",
                    "sender_id": "972551235@lid",
                    "message_id": "m-pdf-1",
                    "text": "",
                    "timestamp": 1700000000,
                    "media_url": "http://gateway/media/doc.pdf",
                    "media_type": "document",
                    "mime_type": "application/pdf",
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # PDF לא נתפס כתמונה - ההודעה נדלגת (אין text ואין photo)
    assert data["processed"] == 0 or (
        "new_state" not in data.get("responses", [{}])[0]
        or data["responses"][0].get("new_state")
        != CourierState.REGISTER_COLLECT_SELFIE.value
    )


@pytest.mark.asyncio
async def test_whatsapp_admin_can_return_to_main_menu_from_courier_flow(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
    monkeypatch,
):
    """
    רגרסיה: אדמין שנרשם כשליח בווטסאפ עלול להינעל בתפריט שליח.
    לחיצה על # חייבת להחזיר אותו לתפריט הראשי (welcome) ולאפשר שוב גישה לאפשרויות הרישום.
    """
    admin_sender_id = "972501234567@lid"
    # הגדרת מספר האדמין (נרמול מתבצע בצד הקוד)
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "972501234567")

    # יצירת משתמש כאילו נרשם כשליח (מצב שכבר "נתקע" עליו)
    admin_user = await user_factory(
        phone_number=admin_sender_id,
        name="Admin User",
        role=UserRole.COURIER,
        platform="whatsapp",
        approval_status=ApprovalStatus.APPROVED,
    )

    resp = await test_client.post(
        "/api/webhooks/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": admin_sender_id,
                    "sender_id": admin_sender_id,
                    "reply_to": admin_sender_id,
                    "message_id": "m-admin-1",
                    "text": "#",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 1
    assert data["responses"][0]["response"].startswith("welcome")

    # חשוב: אסור לשנות role ב-DB רק בגלל שהמשתמש אדמין (לפי מספר).
    await db_session.refresh(admin_user)
    assert admin_user.role == UserRole.COURIER

    # אימות שנשלחה הודעת welcome בפועל דרך ה-gateway (קריאה אחת לפחות)
    assert mock_whatsapp_gateway.post.call_count >= 1


@pytest.mark.asyncio
async def test_whatsapp_admin_root_menu_works_with_cross_format_normalization(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
    monkeypatch,
):
    """
    רגרסיה: מנהל עם 050 בהגדרות צריך להיות מזוהה גם כש-sender_id מגיע כ-972...@lid.
    בלי נרמול — הפיצ'ר של תפריט ראשי לאדמין לא עובד והמנהל נופל לתפריט שליח.
    """
    admin_sender_id = "972501234567@lid"
    # מספר האדמין בהגדרות בפורמט 050 — שונה מה-sender_id
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "0501234567")

    admin_user = await user_factory(
        phone_number=admin_sender_id,
        name="Admin Cross Format",
        role=UserRole.COURIER,
        platform="whatsapp",
        approval_status=ApprovalStatus.APPROVED,
    )

    resp = await test_client.post(
        "/api/webhooks/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": admin_sender_id,
                    "sender_id": admin_sender_id,
                    "reply_to": admin_sender_id,
                    "message_id": "m-xformat-1",
                    "text": "#",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 1
    # חייב להגיע ל-admin root menu — welcome, לא תפריט שליח
    assert data["responses"][0]["response"].startswith("welcome")
    assert data["responses"][0].get("admin_main_menu") is True

    # role לא אמור להשתנות
    await db_session.refresh(admin_user)
    assert admin_user.role == UserRole.COURIER

    # הודעת welcome נשלחה למספר מההגדרות (0501234567), לא ל-@lid
    assert mock_whatsapp_gateway.post.call_count >= 1
    last_call = mock_whatsapp_gateway.post.call_args
    sent_payload = last_call[1].get("json", {}) if last_call[1] else last_call[0][1] if len(last_call[0]) > 1 else {}
    if "phone" in sent_payload:
        assert "@lid" not in sent_payload["phone"]


@pytest.mark.asyncio
async def test_whatsapp_admin_root_menu_matches_reply_to_or_from_number(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
    monkeypatch,
):
    """
    רגרסיה: sender_id לא מספרי (למשל @lid) עדיין צריך לזהות אדמין
    לפי reply_to/from_number כדי לאפשר חזרה לתפריט הראשי.
    """
    admin_sender_id = "device-abc@lid"
    from_number = "972501234567@c.us"
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "0501234567")

    admin_user = await user_factory(
        phone_number=admin_sender_id,
        name="Admin LID",
        role=UserRole.COURIER,
        platform="whatsapp",
        approval_status=ApprovalStatus.APPROVED,
    )

    resp = await test_client.post(
        "/api/webhooks/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": from_number,
                    "sender_id": admin_sender_id,
                    "reply_to": admin_sender_id,
                    "message_id": "m-admin-lid-1",
                    "text": "#",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 1
    assert data["responses"][0]["response"].startswith("welcome")
    assert data["responses"][0].get("admin_main_menu") is True

    await db_session.refresh(admin_user)
    assert admin_user.role == UserRole.COURIER

    assert mock_whatsapp_gateway.post.call_count >= 1
    last_call = mock_whatsapp_gateway.post.call_args
    sent_payload = (
        last_call[1].get("json", {})
        if last_call[1]
        else last_call[0][1]
        if len(last_call[0]) > 1
        else {}
    )
    if "phone" in sent_payload:
        assert sent_payload["phone"] == "0501234567"


@pytest.mark.asyncio
async def test_whatsapp_admin_station_owner_does_not_lose_role_on_main_menu_reset(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
    monkeypatch,
):
    """
    רגרסיה: אדמין שהוא גם בעל תחנה לא אמור לאבד את התפקיד STATION_OWNER
    רק בגלל ששלח #/תפריט ראשי.
    """
    admin_sender_id = "972599999999@lid"
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "972599999999")

    station_owner_admin = await user_factory(
        phone_number=admin_sender_id,
        name="Station Owner Admin",
        role=UserRole.STATION_OWNER,
        platform="whatsapp",
        approval_status=ApprovalStatus.APPROVED,
    )

    resp = await test_client.post(
        "/api/webhooks/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": admin_sender_id,
                    "sender_id": admin_sender_id,
                    "reply_to": admin_sender_id,
                    "message_id": "m-admin-station-1",
                    "text": "תפריט ראשי",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 1
    assert data["responses"][0]["response"].startswith("welcome")

    await db_session.refresh(station_owner_admin)
    assert station_owner_admin.role == UserRole.STATION_OWNER
    assert mock_whatsapp_gateway.post.call_count >= 1
