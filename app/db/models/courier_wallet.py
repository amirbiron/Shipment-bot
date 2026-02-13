"""
Courier Wallet Model - Balance Tracking
"""
from decimal import Decimal
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.db.database import Base


class CourierWallet(Base):
    """Current balance per courier"""

    __tablename__ = "courier_wallets"

    id = Column(Integer, primary_key=True, index=True)
    courier_id = Column(BigInteger, ForeignKey("users.id"), unique=True, nullable=False)

    balance = Column(Numeric(10, 2), default=Decimal("0.00"))
    credit_limit = Column(Numeric(10, 2), default=Decimal("-500.00"))  # Minimum allowed balance

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    courier = relationship("User")
