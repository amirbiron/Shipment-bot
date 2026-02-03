"""
Unit tests for WalletService.

These tests use the in-memory SQLite async session fixture (db_session)
to validate wallet creation, debit/credit flows, and ledger behavior.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from unittest.mock import AsyncMock

from app.core.config import settings
from app.db.models.user import UserRole
from app.db.models.courier_wallet import CourierWallet
from app.db.models.wallet_ledger import WalletLedger, LedgerEntryType
from app.domain.services.wallet_service import WalletService


@pytest.mark.unit
async def test_get_or_create_wallet_creates_new(user_factory, db_session):
    courier = await user_factory(role=UserRole.COURIER, platform="whatsapp")
    service = WalletService(db_session)

    wallet = await service.get_or_create_wallet(courier.id)

    assert wallet.courier_id == courier.id
    assert wallet.balance == 0.0
    assert wallet.credit_limit == settings.DEFAULT_CREDIT_LIMIT

    result = await db_session.execute(
        select(CourierWallet).where(CourierWallet.courier_id == courier.id)
    )
    persisted = result.scalar_one()
    assert persisted.id == wallet.id


@pytest.mark.unit
async def test_check_can_capture_insufficient_credit(user_factory, wallet_factory, db_session):
    courier = await user_factory(role=UserRole.COURIER, platform="whatsapp")
    await wallet_factory(courier_id=courier.id, balance=-490.0, credit_limit=-500.0)

    service = WalletService(db_session)
    can_capture, reason = await service.check_can_capture(courier.id, fee=20.0)

    assert can_capture is False
    assert "יתרה לא מספיקה" in reason


@pytest.mark.unit
async def test_debit_for_capture_returns_none_when_credit_limit_exceeded(
    user_factory, wallet_factory, db_session
):
    courier = await user_factory(role=UserRole.COURIER, platform="whatsapp")
    await wallet_factory(courier_id=courier.id, balance=-490.0, credit_limit=-500.0)

    service = WalletService(db_session)
    entry = await service.debit_for_capture(
        courier_id=courier.id,
        delivery_id=1,
        fee=20.0,
    )

    assert entry is None

    # Wallet balance should remain unchanged
    wallet = await service.get_or_create_wallet(courier.id)
    assert wallet.balance == -490.0


@pytest.mark.unit
async def test_debit_for_capture_creates_ledger_entry_without_commit(
    user_factory, wallet_factory, delivery_factory, db_session, monkeypatch
):
    sender = await user_factory(
        phone_number="+972501000001",
        role=UserRole.SENDER,
        platform="whatsapp",
    )
    courier = await user_factory(
        phone_number="+972501000002",
        role=UserRole.COURIER,
        platform="whatsapp",
    )
    delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
    await wallet_factory(courier_id=courier.id, balance=100.0, credit_limit=-500.0)

    service = WalletService(db_session)

    # Ensure debit_for_capture does not commit by itself (wallet already exists)
    commit_mock = AsyncMock()
    monkeypatch.setattr(db_session, "commit", commit_mock)

    entry = await service.debit_for_capture(
        courier_id=courier.id,
        delivery_id=delivery.id,
        fee=10.0,
    )

    assert entry is not None
    assert entry.entry_type == LedgerEntryType.DELIVERY_FEE_DEBIT
    assert entry.amount == -10.0
    assert entry.balance_after == 90.0

    wallet = await service.get_or_create_wallet(courier.id)
    assert wallet.balance == 90.0

    commit_mock.assert_not_awaited()

    # Flush should be enough to get an id (still uncommitted)
    await db_session.flush()
    assert entry.id is not None


@pytest.mark.unit
async def test_credit_for_delivery_updates_balance_and_persists_ledger(
    user_factory, wallet_factory, delivery_factory, db_session
):
    sender = await user_factory(
        phone_number="+972501000003",
        role=UserRole.SENDER,
        platform="whatsapp",
    )
    courier = await user_factory(
        phone_number="+972501000004",
        role=UserRole.COURIER,
        platform="whatsapp",
    )
    delivery = await delivery_factory(sender_id=sender.id, fee=10.0)
    await wallet_factory(courier_id=courier.id, balance=50.0, credit_limit=-500.0)

    service = WalletService(db_session)
    entry = await service.credit_for_delivery(
        courier_id=courier.id,
        delivery_id=delivery.id,
        amount=25.0,
    )

    assert entry.entry_type == LedgerEntryType.DELIVERY_COMPLETED_CREDIT
    assert entry.amount == 25.0
    assert entry.balance_after == 75.0

    wallet = await service.get_or_create_wallet(courier.id)
    assert wallet.balance == 75.0

    result = await db_session.execute(
        select(WalletLedger)
        .where(WalletLedger.courier_id == courier.id)
        .where(WalletLedger.delivery_id == delivery.id)
        .where(WalletLedger.entry_type == LedgerEntryType.DELIVERY_COMPLETED_CREDIT)
    )
    assert result.scalar_one().amount == 25.0


@pytest.mark.unit
async def test_get_ledger_history_returns_latest_first(
    user_factory, wallet_factory, delivery_factory, db_session
):
    sender = await user_factory(
        phone_number="+972501000005",
        role=UserRole.SENDER,
        platform="whatsapp",
    )
    courier = await user_factory(
        phone_number="+972501000006",
        role=UserRole.COURIER,
        platform="whatsapp",
    )
    await wallet_factory(courier_id=courier.id, balance=0.0, credit_limit=-500.0)

    service = WalletService(db_session)

    d1 = await delivery_factory(sender_id=sender.id, fee=10.0)
    d2 = await delivery_factory(sender_id=sender.id, fee=10.0)

    await service.credit_for_delivery(courier.id, d1.id, amount=10.0)
    await service.credit_for_delivery(courier.id, d2.id, amount=20.0)

    history = await service.get_ledger_history(courier.id, limit=10)
    assert len(history) == 2
    assert history[0].delivery_id == d2.id
    assert history[1].delivery_id == d1.id

