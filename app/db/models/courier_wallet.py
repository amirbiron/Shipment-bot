"""
Courier Wallet Model - Balance Tracking
"""
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.db.database import Base


class CourierWallet(Base):
    """Current balance per courier"""

    __tablename__ = "courier_wallets"

    id = Column(Integer, primary_key=True, index=True)
    courier_id = Column(BigInteger, ForeignKey("users.id"), unique=True, nullable=False)

    balance = Column(Float, default=0.0)
    credit_limit = Column(Float, default=-500.0)  # Minimum allowed balance

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    courier = relationship("User")
