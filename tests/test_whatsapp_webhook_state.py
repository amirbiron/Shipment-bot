"""
Tests for WhatsApp webhook conversation state persistence.

Regression: after refactors, WhatsApp sender identifier could change between messages
(@lid vs @c.us), causing the bot to "repeat the same question" because the session
was created for a different user record each time.
"""

import pytest
from httpx import AsyncClient

from app.db.models.user import User, UserRole, ApprovalStatus
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
        assert sent_payload["phone"] == "+972501234567"


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


@pytest.mark.asyncio
async def test_dedup_db_idempotency(
    db_session,
):
    """בדיקת יחידה ל-_try_acquire_message ו-_mark_message_completed (DB idempotency)"""
    from app.api.webhooks.whatsapp import _try_acquire_message, _mark_message_completed

    # הודעה ראשונה — אפשר לעבד (commit פנימי ב-_try_acquire_message)
    assert await _try_acquire_message(db_session, "msg-1", "whatsapp") is True

    # אותה הודעה ב-processing — לא מאפשרים עיבוד חוזר
    assert await _try_acquire_message(db_session, "msg-1", "whatsapp") is False

    # סימון כ-completed (commit פנימי ב-_mark_message_completed)
    await _mark_message_completed(db_session, "msg-1")

    # אחרי completed — עדיין חוסם כפילויות
    assert await _try_acquire_message(db_session, "msg-1", "whatsapp") is False

    # הודעה חדשה — אפשר לעבד
    assert await _try_acquire_message(db_session, "msg-2", "whatsapp") is True


@pytest.mark.asyncio
async def test_dedup_stale_message_allows_retry(
    db_session,
):
    """הודעה תקועה ב-processing מעבר ל-threshold מאפשרת retry"""
    from datetime import datetime, timedelta, timezone
    from app.api.webhooks.whatsapp import (
        _try_acquire_message,
        _STALE_PROCESSING_SECONDS,
    )
    from app.db.models.webhook_event import WebhookEvent
    from sqlalchemy import select

    # הכנסת הודעה ראשונה (commit פנימי)
    assert await _try_acquire_message(db_session, "msg-stale-1", "whatsapp") is True

    # הודעה עדיין "טרייה" — לא מאפשרים retry
    assert await _try_acquire_message(db_session, "msg-stale-1", "whatsapp") is False

    # זיוף created_at ישן — הודעה "תקועה"
    from sqlalchemy import update as sa_update
    await db_session.execute(
        sa_update(WebhookEvent)
        .where(WebhookEvent.message_id == "msg-stale-1")
        .values(
            created_at=datetime.now(timezone.utc)
            - timedelta(seconds=_STALE_PROCESSING_SECONDS + 10)
        )
    )
    await db_session.commit()

    # עכשיו ההודעה תקועה — retry מותר (commit פנימי)
    assert await _try_acquire_message(db_session, "msg-stale-1", "whatsapp") is True


# ============================================================================
# מניעת כרטיס נהג כפול (notification idempotency)
# ============================================================================


@pytest.mark.asyncio
async def test_courier_notification_not_sent_twice(
    test_client: AsyncClient,
    mock_whatsapp_gateway,
    db_session,
):
    """
    רגרסיה: כרטיס נהג נשלח פעמיים כשהגטוויי שולח שני webhook calls
    עבור אותה לחיצת כפתור. הנוטיפיקציה חייבת לרוץ רק פעם אחת.
    המפתח כולל timestamp (לדקה) כדי להבחין בין רישומים נפרדים.
    """
    from app.api.webhooks.whatsapp import _try_acquire_message, _mark_message_completed

    user_id = 42
    reg_ts = 1000  # מייצג דקה מסוימת
    notify_key = f"courier_reg_notify_{user_id}_{reg_ts}"

    # ניסיון ראשון — מאפשר שליחה
    assert await _try_acquire_message(db_session, notify_key, "notification") is True
    await _mark_message_completed(db_session, notify_key)

    # ניסיון שני עם אותו מפתח — חוסם שליחה כפולה
    assert await _try_acquire_message(db_session, notify_key, "notification") is False

    # רי-רגיסטרציה (timestamp חדש) — מאפשר שליחה מחדש
    new_reg_ts = 2000
    new_notify_key = f"courier_reg_notify_{user_id}_{new_reg_ts}"
    assert await _try_acquire_message(db_session, new_notify_key, "notification") is True


@pytest.mark.asyncio
async def test_handle_terms_skips_if_already_accepted(
    db_session,
):
    """
    רגרסיה: אם המשתמש כבר אישר תקנון (terms_accepted_at מוגדר)
    ה-handler לא צריך לעבד מחדש — צריך להעביר ל-pending_approval.
    """
    from datetime import datetime
    from app.state_machine.handlers import CourierStateHandler
    from app.state_machine.states import CourierState
    from app.state_machine.manager import StateManager

    # יצירת משתמש שכבר סיים רישום
    user = User(
        phone_number="+972509999888",
        name="Test Courier",
        full_name="Test Courier Full",
        role=UserRole.COURIER,
        platform="whatsapp",
        approval_status=ApprovalStatus.PENDING,
        terms_accepted_at=datetime.utcnow(),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # הגדרת state ל-REGISTER_TERMS (מצב לא תקין — כבר אישר)
    state_manager = StateManager(db_session)
    await state_manager.force_state(
        user.id, "whatsapp", CourierState.REGISTER_TERMS.value
    )

    handler = CourierStateHandler(db_session, platform="whatsapp")
    response, new_state = await handler.handle_message(user, "קראתי ואני מאשר ✅", None)

    # צריך להחזיר PENDING_APPROVAL (דרך _handle_pending_approval) ולא לעבד מחדש
    assert new_state == CourierState.PENDING_APPROVAL.value
    assert "בבדיקה" in response.text or "ממתין" in response.text or "⏳" in response.text


@pytest.mark.asyncio
async def test_get_or_create_user_prefers_real_phone(
    db_session,
):
    """
    כשיוצרים משתמש חדש עם normalized_phone זמין,
    ה-phone_number צריך להיות המספר האמיתי (לא wa: placeholder).
    """
    from app.api.webhooks.whatsapp import get_or_create_user

    user, is_new, norm_phone = await get_or_create_user(
        db_session,
        sender_identifier="some_long_lid_identifier_12345@lid",
        from_number="972501234567@c.us",
        reply_to="972501234567@c.us",
        resolved_phone="972501234567",
    )

    assert is_new is True
    # צריך להשתמש במספר האמיתי, לא ב-wa:placeholder
    assert user.phone_number == "+972501234567"
    assert norm_phone == "+972501234567"


@pytest.mark.asyncio
async def test_get_or_create_user_heals_placeholder_to_real_phone(
    db_session,
):
    """
    כשיש משתמש קיים עם wa:placeholder, ויש לנו עכשיו מספר אמיתי,
    ה-phone_number צריך להתעדכן למספר האמיתי.
    """
    from app.api.webhooks.whatsapp import get_or_create_user
    import hashlib

    # יצירת משתמש עם placeholder
    sender_raw = "some_stable_sender@lid"
    digest = hashlib.sha1(sender_raw.encode("utf-8")).hexdigest()[:17]
    placeholder = f"wa:{digest}"

    existing_user = User(
        phone_number=placeholder,
        platform="whatsapp",
        role=UserRole.SENDER,
    )
    db_session.add(existing_user)
    await db_session.commit()
    await db_session.refresh(existing_user)

    # חיפוש עם אותו sender_id + מספר אמיתי
    user, is_new, norm_phone = await get_or_create_user(
        db_session,
        sender_identifier=sender_raw,
        from_number="972509876543@c.us",
        resolved_phone="972509876543",
    )

    assert is_new is False
    assert user.id == existing_user.id
    # ה-phone_number צריך להתעדכן למספר האמיתי
    assert user.phone_number == "+972509876543"


# ============================================================================
# תיקון באג #227: "פנייה לניהול" לא עובד לבעלי תחנה ושליחים
# ============================================================================


@pytest.mark.asyncio
async def test_whatsapp_station_owner_can_use_admin_contact_button(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
    monkeypatch,
):
    """
    רגרסיה #227: בעל תחנה שלוחץ על "📞 פנייה לניהול" צריך לקבל הנחיה
    לכתוב הודעה — לא תפריט ניהול תחנה.
    """
    sender_id = "972559999999@lid"
    await user_factory(
        phone_number=sender_id,
        name="Station Owner Test",
        role=UserRole.STATION_OWNER,
        platform="whatsapp",
    )

    resp = await test_client.post(
        "/api/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": sender_id,
                    "sender_id": sender_id,
                    "reply_to": sender_id,
                    "message_id": "m-admin-contact-station-1",
                    "text": "📞 פנייה לניהול",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 1

    response_text = data["responses"][0]["response"]
    # חייב להכיל הנחיה לכתוב הודעה
    assert "פנייה לניהול" in response_text
    assert "תועבר להנהלה" in response_text
    # לא צריך להציג תפריט ניהול תחנה
    assert "ניהול בעלים" not in response_text
    assert "ניהול סדרנים" not in response_text
    assert "ארנק תחנה" not in response_text


@pytest.mark.asyncio
async def test_whatsapp_admin_contact_forwards_message_to_admin(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
    monkeypatch,
):
    """
    רגרסיה #227: אחרי לחיצה על "פנייה לניהול", ההודעה הבאה מועברת להנהלה
    ולא נופלת לניתוב לפי תפקיד.
    """
    monkeypatch.setattr(settings, "WHATSAPP_ADMIN_NUMBERS", "972500000001")

    sender_id = "972558888888@lid"
    await user_factory(
        phone_number=sender_id,
        name="Courier Test",
        full_name="שליח בדיקה",
        role=UserRole.COURIER,
        platform="whatsapp",
        approval_status=ApprovalStatus.APPROVED,
    )

    # שלב 1: לחיצה על "פנייה לניהול"
    resp1 = await test_client.post(
        "/api/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": sender_id,
                    "sender_id": sender_id,
                    "reply_to": sender_id,
                    "message_id": "m-admin-fwd-1",
                    "text": "📞 פנייה לניהול",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp1.status_code == 200
    assert "תועבר להנהלה" in resp1.json()["responses"][0]["response"]

    # שלב 2: שליחת הודעה — צריכה להיות מועברת להנהלה
    resp2 = await test_client.post(
        "/api/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": sender_id,
                    "sender_id": sender_id,
                    "reply_to": sender_id,
                    "message_id": "m-admin-fwd-2",
                    "text": "שלום, יש לי בעיה עם החשבון",
                    "timestamp": 1700000001,
                }
            ]
        },
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["processed"] == 1

    response_text = data2["responses"][0]["response"]
    # חייב להכיל אישור שההודעה נשלחה (לא תפריט שליח/תחנה)
    assert "נשלחה להנהלה" in response_text
    assert "ניהול בעלים" not in response_text


@pytest.mark.asyncio
async def test_whatsapp_admin_contact_back_button_returns_to_menu(
    test_client: AsyncClient,
    db_session,
    user_factory,
    mock_whatsapp_gateway,
):
    """
    אחרי לחיצה על "פנייה לניהול", לחיצה על "חזרה לתפריט" מחזירה
    לתפריט לפי תפקיד במקום להעביר כהודעה.
    """
    sender_id = "972557777777@lid"
    await user_factory(
        phone_number=sender_id,
        name="Back Test",
        role=UserRole.STATION_OWNER,
        platform="whatsapp",
    )

    # שלב 1: לחיצה על "פנייה לניהול"
    resp1 = await test_client.post(
        "/api/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": sender_id,
                    "sender_id": sender_id,
                    "reply_to": sender_id,
                    "message_id": "m-admin-back-1",
                    "text": "📞 פנייה לניהול",
                    "timestamp": 1700000000,
                }
            ]
        },
    )
    assert resp1.status_code == 200

    # שלב 2: לחיצה על "חזרה" — לא צריך להעביר הודעה
    resp2 = await test_client.post(
        "/api/whatsapp/webhook",
        json={
            "messages": [
                {
                    "from_number": sender_id,
                    "sender_id": sender_id,
                    "reply_to": sender_id,
                    "message_id": "m-admin-back-2",
                    "text": "🔙 חזרה לתפריט",
                    "timestamp": 1700000001,
                }
            ]
        },
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["processed"] == 1
    # לא צריך לשלוח "נשלחה להנהלה" — המשתמש לחץ חזרה
    assert "נשלחה להנהלה" not in data2["responses"][0]["response"]


@pytest.mark.asyncio
async def test_get_or_create_user_finds_by_real_phone_after_heal(
    db_session,
):
    """
    אחרי ריפוי phone_number למספר אמיתי, חיפוש עם sender_id חדש
    צריך למצוא את המשתמש לפי normalized_phone (ולא ליצור כפילות).
    """
    from app.api.webhooks.whatsapp import get_or_create_user

    # יצירת משתמש ישירות עם מספר אמיתי (כאילו כבר רופא)
    existing_user = User(
        phone_number="+972507777888",
        platform="whatsapp",
        role=UserRole.COURIER,
        approval_status=ApprovalStatus.PENDING,
    )
    db_session.add(existing_user)
    await db_session.commit()
    await db_session.refresh(existing_user)

    # חיפוש עם sender_id שונה לגמרי אבל אותו מספר טלפון
    user, is_new, norm_phone = await get_or_create_user(
        db_session,
        sender_identifier="completely_new_lid@lid",
        from_number="972507777888@c.us",
        resolved_phone="972507777888",
    )

    assert is_new is False
    assert user.id == existing_user.id


# ============================================================================
# BSUID (Meta Cloud API) — dual-key lookup ב-get_or_create_user
# ============================================================================


@pytest.mark.asyncio
async def test_get_or_create_user_bsuid_lookup_wins_over_phone(
    db_session,
):
    """
    כשמסופקים גם BSUID וגם מספר טלפון, ה-BSUID מנצח ומחזיר את המשתמש
    שמזוהה ב-external_user_id — גם אם מספר הטלפון בבקשה שייך למשתמש אחר.
    """
    from app.api.webhooks.whatsapp import get_or_create_user

    user_with_bsuid = User(
        phone_number="+972500000001",
        platform="whatsapp",
        role=UserRole.SENDER,
        external_user_id="IL.abc123bsuid",
    )
    user_with_only_phone = User(
        phone_number="+972500000002",
        platform="whatsapp",
        role=UserRole.SENDER,
    )
    db_session.add_all([user_with_bsuid, user_with_only_phone])
    await db_session.commit()
    await db_session.refresh(user_with_bsuid)
    await db_session.refresh(user_with_only_phone)

    # הטלפון תואם ל-user השני, אבל ה-BSUID תואם ל-user הראשון — ה-BSUID מנצח.
    user, is_new, _norm = await get_or_create_user(
        db_session,
        sender_identifier="+972500000002",
        resolved_phone="972500000002",
        external_user_id="IL.abc123bsuid",
    )

    assert is_new is False
    assert user.id == user_with_bsuid.id


@pytest.mark.asyncio
async def test_get_or_create_user_bsuid_miss_falls_back_to_phone(
    db_session,
):
    """
    BSUID שלא קיים ב-DB — נופלים ללוגיקת phone הקיימת ומחזירים את המשתמש
    לפי מספר הטלפון.
    """
    from app.api.webhooks.whatsapp import get_or_create_user

    existing = User(
        phone_number="+972501111111",
        platform="whatsapp",
        role=UserRole.SENDER,
    )
    db_session.add(existing)
    await db_session.commit()
    await db_session.refresh(existing)

    user, is_new, _norm = await get_or_create_user(
        db_session,
        sender_identifier="+972501111111",
        resolved_phone="972501111111",
        external_user_id="IL.unknown_bsuid",
    )

    assert is_new is False
    assert user.id == existing.id


@pytest.mark.asyncio
async def test_get_or_create_user_bsuid_heals_placeholder_phone(
    db_session,
):
    """
    משתמש שזוהה דרך BSUID עם phone_number של placeholder (wa:...) —
    כשיש עכשיו מספר אמיתי, ה-phone_number מעודכן למספר האמיתי.
    """
    import hashlib

    from app.api.webhooks.whatsapp import get_or_create_user

    sender_raw = "bsuid_user_old_lid@lid"
    digest = hashlib.sha1(sender_raw.encode("utf-8")).hexdigest()[:17]
    placeholder = f"wa:{digest}"

    existing = User(
        phone_number=placeholder,
        platform="whatsapp",
        role=UserRole.SENDER,
        external_user_id="IL.heal_test_bsuid",
    )
    db_session.add(existing)
    await db_session.commit()
    await db_session.refresh(existing)

    user, is_new, norm_phone = await get_or_create_user(
        db_session,
        sender_identifier=sender_raw,
        resolved_phone="972502222333",
        external_user_id="IL.heal_test_bsuid",
    )

    assert is_new is False
    assert user.id == existing.id
    # המספר התעדכן מ-placeholder למספר האמיתי
    assert user.phone_number == "+972502222333"
    assert norm_phone == "+972502222333"


@pytest.mark.asyncio
async def test_get_or_create_user_without_bsuid_uses_phone_lookup(
    db_session,
):
    """
    לקוח שלא מעביר BSUID (WPPConnect webhook) — מתנהג כמו קודם,
    ללא regression.
    """
    from app.api.webhooks.whatsapp import get_or_create_user

    existing = User(
        phone_number="+972503333444",
        platform="whatsapp",
        role=UserRole.SENDER,
    )
    db_session.add(existing)
    await db_session.commit()
    await db_session.refresh(existing)

    user, is_new, _norm = await get_or_create_user(
        db_session,
        sender_identifier="+972503333444",
        resolved_phone="972503333444",
        # ללא external_user_id — דמיית WPPConnect
    )

    assert is_new is False
    assert user.id == existing.id
