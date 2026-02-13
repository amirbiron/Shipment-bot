"""
Station Wallet Model - ארנק תחנה

כל תחנה מקבלת 10% עמלה מכל משלוח שמבוצע דרכה.
"""
from decimal import Decimal
from datetime import datetime
from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.db.database import Base


class StationWallet(Base):
    """יתרת ארנק תחנה"""

    __tablename__ = "station_wallets"

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(Integer, ForeignKey("stations.id"), unique=True, nullable=False)

    balance = Column(Numeric(10, 2), default=Decimal("0.00"))
    commission_rate = Column(Numeric(10, 2), default=Decimal("0.10"))  # 10% ברירת מחדל

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    station = relationship("Station")
