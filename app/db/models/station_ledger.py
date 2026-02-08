"""
Station Ledger Model - היסטוריית תנועות פיננסיות של תחנה

רישום בלתי-הפיך של כל תנועה פיננסית בארנק התחנה.
"""
import enum
from datetime import datetime
from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, String, Enum as SQLEnum

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
    amount = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)

    description = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
