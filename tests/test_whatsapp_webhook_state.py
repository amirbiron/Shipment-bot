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
    msg_counter = [0]

    async def post(text: str, reply_to: str) -> dict:
        msg_counter[0] += 1
        payload = {
            "messages": [
                {
                    "from_number": reply_to,  # legacy field (can change)
                    "sender_id": sender_id,  # stable field (must not change)
                    "reply_to": reply_to,  # where to actually reply
                    "message_id": f"m-state-{msg_counter[0]}",
                    "text": text,
                    "timestamp": 1700000000,
                }
            ]
        }
        r = await test_client.post("/api/whatsapp/webhook", json=payload)
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


@pytest.mark.integration
async def test_whatsapp_state_persists_with_long_sender_id_hashed(
    test_client: AsyncClient,
    mock_whatsapp_gateway,
):
    """
    רגרסיה: sender_id ארוך (מעל 20 תווים) נשמר כ-wa:<hash>.
    ה-lookup חייב להשתמש באותו hash כדי שה-state לא יישבר בין הודעות.
    """
    long_sender_id = "very-long-stable-sender-identifier-1234567890@lid"
    msg_counter = [0]

    async def post(text: str, reply_to: str) -> dict:
        msg_counter[0] += 1
        payload = {
            "messages": [
                {
                    "from_number": reply_to,
                    "sender_id": long_sender_id,
                    "reply_to": reply_to,
                    "message_id": f"m-long-{msg_counter[0]}",
                    "text": text,
                    "timestamp": 1700000000,
                }
            ]
        }
        r = await test_client.post("/api/whatsapp/webhook", json=payload)
        assert r.status_code == 200
        return r.json()

    # 1) יצירת משתמש חדש (welcome)
    res1 = await post("שלום", reply_to="972501234567@c.us")
    assert res1["processed"] == 1
    assert res1["responses"][0]["new_user"] is True

    # 2) התחלת זרימת שולח — צריך להתקדם (לא להיווצר משתמש נוסף/להיתקע)
    res2 = await post("📦 אני רוצה לשלוח חבילה", reply_to="972501234567@lid")
    assert res2["processed"] == 1
    assert res2["responses"][0]["new_state"] == "SENDER.REGISTER.COLLECT_NAME"


@pytest.mark.asyncio
async def test_whatsapp_long_sender_id_raw_and_hashed_records_do_not_crash(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
):
    """
    רגרסיה: קיימות שתי רשומות משתמש עבור אותו sender_id ארוך:
    1) phone_number = הערך הגולמי (אפשרי בסביבות/גרסאות ישנות או ב-SQLite)
    2) phone_number = wa:<hash> (המצב התקין בקוד החדש)

    ה-webhook לא אמור לקרוס בגלל MultipleResultsFound, וחייב לבחור אחת דטרמיניסטית.
    """
    import hashlib

    sender_id_raw = "very-long-stable-sender-identifier-1234567890@lid"
    digest = hashlib.sha1(sender_id_raw.encode("utf-8")).hexdigest()[:17]
    sender_id_hashed = f"wa:{digest}"

    # יוצרים שתי רשומות שונות (כמו מצב "תמיכה לאחור" אמיתי)
    await user_factory(phone_number=sender_id_raw, name="Legacy Raw", platform="whatsapp")
    await user_factory(phone_number=sender_id_hashed, name="Hashed", platform="whatsapp")

    resp = await test_client.post(
        "/api/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": "972501234567@c.us",
                    "sender_id": sender_id_raw,
                    "reply_to": "972501234567@c.us",
                    "message_id": "m-dupe-1",
                    "text": "שלום",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 1


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
        "/api/whatsapp/webhook",
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
        "/api/whatsapp/webhook",
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
        "/api/whatsapp/webhook",
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

    # אדמין שהיה שליח חוזר להיות שולח — כדי שהודעות הבאות לא ייפלו ל-CourierStateHandler
    await db_session.refresh(admin_user)
    assert admin_user.role == UserRole.SENDER

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
        "/api/whatsapp/webhook",
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

    # אדמין שהיה שליח חוזר להיות שולח
    await db_session.refresh(admin_user)
    assert admin_user.role == UserRole.SENDER

    # הודעת welcome נשלחה — כשהערך בהגדרות חסר סיומת, מעדיפים מזהה עם סיומת (sender_id/@lid)
    assert mock_whatsapp_gateway.post.call_count >= 1
    last_call = mock_whatsapp_gateway.post.call_args
    sent_payload = last_call[1].get("json", {}) if last_call[1] else last_call[0][1] if len(last_call[0]) > 1 else {}
    if "phone" in sent_payload:
        # הערך בהגדרות הוא 0501234567 (ללא סיומת) — הקוד מעדיף את המזהה המקורי עם סיומת
        assert sent_payload["phone"] == admin_sender_id


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
    from_number = "972501234567"
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "0501234567")

    admin_user = await user_factory(
        phone_number=admin_sender_id,
        name="Admin LID",
        role=UserRole.COURIER,
        platform="whatsapp",
        approval_status=ApprovalStatus.APPROVED,
    )

    resp = await test_client.post(
        "/api/whatsapp/webhook",
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

    # אדמין שהיה שליח חוזר להיות שולח
    await db_session.refresh(admin_user)
    assert admin_user.role == UserRole.SENDER

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
        "/api/whatsapp/webhook",
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


@pytest.mark.asyncio
async def test_whatsapp_admin_returns_to_main_menu_after_courier_entry_via_context_flag(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
    monkeypatch,
):
    """
    רגרסיה: אדמין שנכנס לזרימת שליח ואז לוחץ # חוזר לתפריט ראשי —
    גם אם זיהוי אדמין לפי מספר נכשל (LID ששונה מהמספר בהגדרות).
    דגל entered_as_admin בקונטקסט משמש כ-fallback.

    סימולציה: בשלב 1 הגטוויי שולח from_number אמיתי ואדמין מזוהה.
    בשלב 2 הגטוויי שולח רק LID — זיהוי אדמין נכשל, fallback בקונטקסט עובד.
    """
    # LID שמשמש כ-sender_id יציב (כמו בפרודקשן)
    admin_lid = "9999888877776666@lid"
    admin_phone = "972501234567"
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", admin_phone)

    # יצירת משתמש עם ה-LID כ-phone_number (כמו שנוצר בפרודקשן)
    admin_user = await user_factory(
        phone_number=admin_lid,
        name="Admin Fallback",
        role=UserRole.SENDER,
        platform="whatsapp",
    )

    # שלב 1: הגטוויי שולח sender_id=LID אבל from_number=מספר אמיתי → אדמין מזוהה
    resp1 = await test_client.post(
        "/api/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": admin_phone,
                    "sender_id": admin_lid,
                    "reply_to": admin_lid,
                    "message_id": "m-enter-courier-1",
                    "text": "שליח",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp1.status_code == 200
    await db_session.refresh(admin_user)
    assert admin_user.role == UserRole.COURIER

    # שלב 2: הגטוויי שולח רק LID בכל השדות → זיהוי אדמין נכשל
    # הקוד צריך לזהות entered_as_admin מהקונטקסט ולהחזיר לתפריט ראשי
    resp2 = await test_client.post(
        "/api/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": admin_lid,
                    "sender_id": admin_lid,
                    "reply_to": admin_lid,
                    "message_id": "m-hash-back-1",
                    "text": "#",
                    "timestamp": 1700000001,
                }
            ]
        },
    )
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["processed"] == 1
    # הפעם הנתיב הוא fallback ולא admin_main_menu
    assert data["responses"][0]["response"].startswith("welcome")

    # אדמין חזר להיות שולח
    await db_session.refresh(admin_user)
    assert admin_user.role == UserRole.SENDER


@pytest.mark.asyncio
async def test_whatsapp_approved_courier_non_admin_stays_courier_on_hash(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
    monkeypatch,
):
    """
    שליח מאושר רגיל (לא אדמין) שלוחץ # חוזר לתפריט שליח — לא לתפריט ראשי.
    """
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "972500000000")

    courier_user = await user_factory(
        phone_number="972521111111@lid",
        name="Regular Courier",
        role=UserRole.COURIER,
        platform="whatsapp",
        approval_status=ApprovalStatus.APPROVED,
    )

    resp = await test_client.post(
        "/api/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": "972521111111@lid",
                    "sender_id": "972521111111@lid",
                    "reply_to": "972521111111@lid",
                    "message_id": "m-courier-hash-1",
                    "text": "#",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 1

    # שליח מאושר רגיל נשאר שליח — לא מורד לשולח
    await db_session.refresh(courier_user)
    assert courier_user.role == UserRole.COURIER


# ============================================================================
# Deduplication
# ============================================================================


@pytest.mark.asyncio
async def test_whatsapp_duplicate_message_skipped(
    test_client: AsyncClient,
    mock_whatsapp_gateway,
):
    """הודעה עם אותו message_id נדלגת (deduplication) ולא מעובדת פעמיים"""
    payload = {
        "messages": [
            {
                "from_number": "972501112222@c.us",
                "sender_id": "972501112222@lid",
                "reply_to": "972501112222@c.us",
                "message_id": "m-dedup-test-1",
                "text": "שלום",
                "timestamp": 1700000000,
            }
        ]
    }

    # שליחה ראשונה — חייבת להתעבד
    resp1 = await test_client.post("/api/whatsapp/webhook", json=payload)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["processed"] == 1

    # שליחה שנייה עם אותו message_id — חייבת להידלג
    resp2 = await test_client.post("/api/whatsapp/webhook", json=payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["processed"] == 0


@pytest.mark.unit
def test_dedup_function_detects_duplicates():
    """בדיקת יחידה ל-_is_duplicate_message"""
    from app.api.webhooks.whatsapp import _is_duplicate_message, _processed_messages
    _processed_messages.clear()

    # הודעה ראשונה — לא כפולה
    assert _is_duplicate_message("msg-1") is False
    # אותה הודעה — כפולה
    assert _is_duplicate_message("msg-1") is True
    # הודעה חדשה — לא כפולה
    assert _is_duplicate_message("msg-2") is False

    _processed_messages.clear()
