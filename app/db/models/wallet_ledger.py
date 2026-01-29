"""
Wallet Ledger Model - Immutable Transaction History
"""
import enum
from datetime import datetime
from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, String, Enum as SQLEnum, UniqueConstraint

from app.db.database import Base


class LedgerEntryType(str, enum.Enum):
    DELIVERY_FEE_DEBIT = "delivery_fee_debit"
    DELIVERY_COMPLETED_CREDIT = "delivery_completed_credit"
    MANUAL_CREDIT = "manual_credit"
    MANUAL_DEBIT = "manual_debit"
    REFUND = "refund"


class WalletLedger(Base):
    """Immutable transaction history preventing double-debit"""

    __tablename__ = "wallet_ledger"

    id = Column(Integer, primary_key=True, index=True)
    courier_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    delivery_id = Column(Integer, ForeignKey("deliveries.id"), nullable=True)

    entry_type = Column(SQLEnum(LedgerEntryType), nullable=False)
    amount = Column(Float, nullable=False)  # Positive for credit, negative for debit
    balance_after = Column(Float, nullable=False)

    description = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Prevent duplicate charges
    __table_args__ = (
        UniqueConstraint('courier_id', 'delivery_id', 'entry_type', name='uq_courier_delivery_type'),
    )
