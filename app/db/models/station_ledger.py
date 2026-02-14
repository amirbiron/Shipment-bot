"""
Station Ledger Model - היסטוריית תנועות פיננסיות של תחנה

רישום בלתי-הפיך של כל תנועה פיננסית בארנק התחנה.
"""
import enum
from datetime import datetime
from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey, String, Enum as SQLEnum, UniqueConstraint

from app.db.database import Base


class StationLedgerEntryType(str, enum.Enum):
    COMMISSION_CREDIT = "commission_credit"  # עמלה ממשלוח שהושלם
    MANUAL_CHARGE = "manual_charge"  # חיוב ידני
    WITHDRAWAL = "withdrawal"  # משיכת כספים


class StationLedger(Base):
    """היסטוריית תנועות פיננסיות של תחנה"""

    __tablename__ = "station_ledger"

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False, index=True)
    delivery_id = Column(Integer, ForeignKey("deliveries.id"), nullable=True)

    entry_type = Column(SQLEnum(StationLedgerEntryType), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    balance_after = Column(Numeric(10, 2), nullable=False)

    description = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # מניעת כפילות עמלות — בדומה ל-wallet_ledger
    __table_args__ = (
        UniqueConstraint('station_id', 'delivery_id', 'entry_type', name='uq_station_delivery_type'),
    )
