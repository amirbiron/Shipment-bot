"""
בדיקות לוידוא שעמודות כספיות משתמשות ב-Numeric(10,2) ולא ב-Float.

ראה: https://github.com/amirbiron/Shipment-bot/issues/178
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Numeric, inspect

from app.db.models.courier_wallet import CourierWallet
from app.db.models.wallet_ledger import WalletLedger
from app.db.models.station_wallet import StationWallet
from app.db.models.station_ledger import StationLedger
from app.db.models.manual_charge import ManualCharge
from app.db.models.delivery import Delivery
from app.db.models.user import UserRole


# ============================================================================
# בדיקות סוג עמודה — ולידציה שכל השדות הכספיים הם Numeric
# ============================================================================

_EXPECTED_NUMERIC_COLUMNS = [
    (CourierWallet, "balance"),
    (CourierWallet, "credit_limit"),
    (WalletLedger, "amount"),
    (WalletLedger, "balance_after"),
    (StationWallet, "balance"),
    (StationWallet, "commission_rate"),
    (StationLedger, "amount"),
    (StationLedger, "balance_after"),
    (ManualCharge, "amount"),
    (Delivery, "fee"),
]


@pytest.mark.unit
@pytest.mark.parametrize("model_class, column_name", _EXPECTED_NUMERIC_COLUMNS)
def test_financial_column_is_numeric(model_class, column_name):
    """כל עמודה כספית חייבת להיות Numeric(10,2) ולא Float"""
    mapper = inspect(model_class)
    col = mapper.columns[column_name]
    col_type = col.type

    assert isinstance(col_type, Numeric), (
        f"{model_class.__name__}.{column_name} הוא {type(col_type).__name__} "
        f"במקום Numeric — סיכון לאובדן דיוק בחישובים כספיים"
    )
    assert col_type.precision == 10, (
        f"{model_class.__name__}.{column_name}: precision צפוי 10, קיבלנו {col_type.precision}"
    )
    assert col_type.scale == 2, (
        f"{model_class.__name__}.{column_name}: scale צפוי 2, קיבלנו {col_type.scale}"
    )


# ============================================================================
# בדיקות דיוק פיננסי — ולידציה שלא נוצרת סטייה בחישובים
# ============================================================================

@pytest.mark.unit
async def test_wallet_balance_precision(user_factory, wallet_factory, db_session):
    """ערכים כספיים נשמרים בדיוק של 2 ספרות עשרוניות"""
    courier = await user_factory(role=UserRole.COURIER, platform="whatsapp")
    wallet = await wallet_factory(courier_id=courier.id, balance=100.10, credit_limit=-500.0)

    assert float(wallet.balance) == pytest.approx(100.10)
    assert float(wallet.credit_limit) == pytest.approx(-500.0)


@pytest.mark.unit
async def test_delivery_fee_precision(user_factory, delivery_factory, db_session):
    """עמלת משלוח נשמרת בדיוק"""
    sender = await user_factory(
        phone_number="+972509990001",
        role=UserRole.SENDER,
        platform="whatsapp",
    )
    delivery = await delivery_factory(sender_id=sender.id, fee=29.99)

    assert float(delivery.fee) == pytest.approx(29.99)


@pytest.mark.unit
async def test_classic_float_drift_avoided(user_factory, wallet_factory, db_session):
    """0.1 + 0.2 == 0.3 — בדיקה שאין drift של floating-point"""
    courier = await user_factory(role=UserRole.COURIER, platform="whatsapp")
    # 0.1 + 0.2 ב-float רגיל נותן 0.30000000000000004
    wallet = await wallet_factory(courier_id=courier.id, balance=0.30, credit_limit=-500.0)

    assert float(wallet.balance) == pytest.approx(0.30)
