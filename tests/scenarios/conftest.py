"""
Fixtures ו-helpers לבדיקות תרחיש מקצה לקצה.

מספק:
- בוני payload ל-Telegram ו-WhatsApp
- פונקציות שליחה תמציתיות
- fixtures ליצירת תחנות, סדרנים וארנקי תחנה
- פונקציות אימות DB (סטטוס משלוח, outbox, ארנק)
"""
import pytest
from unittest.mock import patch, AsyncMock
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_wallet import StationWallet
from app.db.models.delivery import Delivery
from app.db.models.outbox_message import OutboxMessage
from app.db.models.courier_wallet import CourierWallet
from app.db.models.wallet_ledger import WalletLedger
from app.core.config import settings


# ============================================================================
# בוני Payload — Telegram
# ============================================================================

_update_counter = 0


def _next_update_id() -> int:
    """מייצר update_id ייחודי למניעת כפילויות"""
    global _update_counter
    _update_counter += 1
    return _update_counter


def build_tg_message(
    chat_id: int,
    text: str,
    *,
    name: str = "Test",
) -> dict:
    """בניית payload הודעת טקסט טלגרם"""
    uid = _next_update_id()
    return {
        "update_id": uid,
        "message": {
            "message_id": uid,
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
            "date": 1700000000 + uid,
            "from": {"id": chat_id, "first_name": name},
        },
    }


def build_tg_callback(
    chat_id: int,
    data: str,
    *,
    name: str = "Test",
) -> dict:
    """בניית payload לחיצת כפתור inline טלגרם"""
    uid = _next_update_id()
    return {
        "update_id": uid,
        "callback_query": {
            "id": f"cb-{uid}",
            "data": data,
            "from": {"id": chat_id, "first_name": name},
            "message": {
                "message_id": uid,
                "chat": {"id": chat_id, "type": "private"},
                "text": "",
                "date": 1700000000 + uid,
            },
        },
    }


def build_tg_photo(
    chat_id: int,
    file_id: str = "test_photo_file_id",
    *,
    name: str = "Test",
) -> dict:
    """בניית payload הודעת תמונה טלגרם"""
    uid = _next_update_id()
    return {
        "update_id": uid,
        "message": {
            "message_id": uid,
            "chat": {"id": chat_id, "type": "private"},
            "text": "",
            "date": 1700000000 + uid,
            "from": {"id": chat_id, "first_name": name},
            "photo": [
                {
                    "file_id": file_id,
                    "file_unique_id": f"u_{file_id}_{uid}",
                    "width": 100,
                    "height": 100,
                }
            ],
        },
    }


# ============================================================================
# בוני Payload — WhatsApp
# ============================================================================

_wa_msg_counter = 0


def build_wa_message(
    phone: str,
    text: str,
) -> dict:
    """בניית payload הודעת טקסט וואטסאפ"""
    global _wa_msg_counter
    _wa_msg_counter += 1
    return {
        "messages": [
            {
                "from_number": f"{phone}@c.us",
                "sender_id": f"{phone}@lid",
                "reply_to": f"{phone}@c.us",
                "message_id": f"wa-msg-{_wa_msg_counter}",
                "text": text,
                "timestamp": 1700000000 + _wa_msg_counter,
            }
        ]
    }


# ============================================================================
# פונקציות שליחה תמציתיות
# ============================================================================

async def send_tg(client, chat_id: int, text: str, **kwargs) -> dict:
    """שליחת הודעת טקסט לטלגרם webhook — assert 200 ומחזיר JSON"""
    resp = await client.post(
        "/api/telegram/webhook",
        json=build_tg_message(chat_id, text, **kwargs),
    )
    assert resp.status_code == 200, f"Telegram webhook returned {resp.status_code}: {resp.text}"
    return resp.json()


async def send_tg_callback(client, chat_id: int, data: str, **kwargs) -> dict:
    """שליחת לחיצת כפתור inline לטלגרם webhook"""
    resp = await client.post(
        "/api/telegram/webhook",
        json=build_tg_callback(chat_id, data, **kwargs),
    )
    assert resp.status_code == 200, f"Telegram callback returned {resp.status_code}: {resp.text}"
    return resp.json()


async def send_tg_photo(client, chat_id: int, file_id: str = "test_photo", **kwargs) -> dict:
    """שליחת תמונה לטלגרם webhook"""
    resp = await client.post(
        "/api/telegram/webhook",
        json=build_tg_photo(chat_id, file_id, **kwargs),
    )
    assert resp.status_code == 200, f"Telegram photo returned {resp.status_code}: {resp.text}"
    return resp.json()


async def send_wa(client, phone: str, text: str) -> dict:
    """שליחת הודעה לוואטסאפ webhook"""
    resp = await client.post(
        "/api/whatsapp/webhook",
        json=build_wa_message(phone, text),
    )
    assert resp.status_code == 200, f"WhatsApp webhook returned {resp.status_code}: {resp.text}"
    return resp.json()


# ============================================================================
# Fixtures — תחנות וסדרנים
# ============================================================================

@pytest.fixture
def station_factory(db_session: AsyncSession):
    """יצירת תחנת בדיקה"""
    async def _create(
        name: str = "תחנת בדיקה",
        owner_id: int = 1,
        public_group_chat_id: Optional[str] = None,
        private_group_chat_id: Optional[str] = None,
    ) -> Station:
        station = Station(
            name=name,
            owner_id=owner_id,
            public_group_chat_id=public_group_chat_id,
            private_group_chat_id=private_group_chat_id,
            public_group_platform="telegram" if public_group_chat_id else None,
            private_group_platform="telegram" if private_group_chat_id else None,
        )
        db_session.add(station)
        await db_session.commit()
        await db_session.refresh(station)
        return station

    return _create


@pytest.fixture
def dispatcher_factory(db_session: AsyncSession):
    """קישור סדרן לתחנה"""
    async def _create(station_id: int, user_id: int) -> StationDispatcher:
        sd = StationDispatcher(
            station_id=station_id,
            user_id=user_id,
        )
        db_session.add(sd)
        await db_session.commit()
        await db_session.refresh(sd)
        return sd

    return _create


@pytest.fixture
def station_wallet_factory(db_session: AsyncSession):
    """יצירת ארנק תחנה"""
    async def _create(
        station_id: int,
        balance: float = 0.0,
        commission_rate: float = 0.10,
    ) -> StationWallet:
        wallet = StationWallet(
            station_id=station_id,
            balance=balance,
            commission_rate=commission_rate,
        )
        db_session.add(wallet)
        await db_session.commit()
        await db_session.refresh(wallet)
        return wallet

    return _create


@pytest.fixture
def configure_admin():
    """הגדרת admin chat IDs וטוקן בוט לבדיקות שדורשות פעולות אדמין"""
    with patch.object(settings, "TELEGRAM_ADMIN_CHAT_IDS", "99999"), \
         patch.object(settings, "TELEGRAM_ADMIN_CHAT_ID", "99999"), \
         patch.object(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token"):
        yield


# ============================================================================
# איפוס מונים וחסימת קריאות חיצוניות בין בדיקות
# ============================================================================

@pytest.fixture(autouse=True)
def _reset_counters():
    """איפוס מוני payload בין בדיקות"""
    global _update_counter, _wa_msg_counter
    _update_counter = 0
    _wa_msg_counter = 0
    yield


@pytest.fixture(autouse=True)
def _mock_outbound_apis():
    """חסימת קריאות HTTP יוצאות לטלגרם/וואטסאפ — מונע קריאות אמיתיות"""
    with patch(
        "app.api.webhooks.telegram.send_telegram_message",
        new_callable=AsyncMock,
    ), patch(
        "app.api.webhooks.telegram.answer_callback_query",
        new_callable=AsyncMock,
    ), patch(
        "app.api.webhooks.telegram.send_welcome_message",
        new_callable=AsyncMock,
    ), patch(
        "app.domain.services.admin_notification_service.AdminNotificationService._send_telegram_message",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.domain.services.admin_notification_service.AdminNotificationService._send_telegram_message_with_inline_keyboard",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.domain.services.admin_notification_service.AdminNotificationService._forward_photo",
        new_callable=AsyncMock,
        return_value=True,
    ):
        yield


# ============================================================================
# פונקציות אימות DB
# ============================================================================

async def assert_delivery_status(
    db_session: AsyncSession,
    delivery_id: int,
    expected_status,
) -> Delivery:
    """אימות סטטוס משלוח — שליפה טרייה מ-DB, מחזיר את המשלוח"""
    result = await db_session.execute(
        select(Delivery).where(Delivery.id == delivery_id).execution_options(
            populate_existing=True
        )
    )
    delivery = result.scalar_one()
    assert delivery.status == expected_status, (
        f"צפי: {expected_status}, בפועל: {delivery.status}"
    )
    return delivery


async def assert_outbox_count(
    db_session: AsyncSession,
    message_type: str,
    min_count: int = 1,
) -> None:
    """אימות שיש לפחות min_count הודעות outbox מסוג נתון"""
    result = await db_session.execute(
        select(func.count(OutboxMessage.id)).where(
            OutboxMessage.message_type == message_type
        )
    )
    count = result.scalar()
    assert count >= min_count, (
        f"צפי >= {min_count} הודעות outbox מסוג '{message_type}', נמצאו {count}"
    )


async def assert_wallet_balance(
    db_session: AsyncSession,
    courier_id: int,
    expected_balance: float,
) -> CourierWallet:
    """אימות יתרת ארנק שליח — שליפה טרייה מ-DB"""
    result = await db_session.execute(
        select(CourierWallet).where(
            CourierWallet.courier_id == courier_id
        ).execution_options(populate_existing=True)
    )
    wallet = result.scalar_one()
    assert abs(wallet.balance - expected_balance) < 0.01, (
        f"צפי: {expected_balance}, בפועל: {wallet.balance}"
    )
    return wallet


async def assert_ledger_count(
    db_session: AsyncSession,
    courier_id: int,
    expected_count: int,
) -> None:
    """אימות מספר רשומות ledger של שליח"""
    result = await db_session.execute(
        select(func.count(WalletLedger.id)).where(
            WalletLedger.courier_id == courier_id
        )
    )
    count = result.scalar()
    assert count == expected_count, (
        f"צפי: {expected_count} רשומות ledger, נמצאו {count}"
    )
