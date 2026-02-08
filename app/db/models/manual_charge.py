"""
Manual Charge Model - חיוב ידני

סדרן יכול להוסיף חיוב ידני לנהג עבור משלוחים שנסגרו מחוץ למערכת.
כולל: שם הנהג, סכום, פרטי המשלוח.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, Float, DateTime, ForeignKey, String, Text

from app.db.database import Base


class ManualCharge(Base):
    """חיוב ידני שנוצר ע"י סדרן"""

    __tablename__ = "manual_charges"

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False, index=True)
    dispatcher_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)

    # פרטי החיוב
    driver_name = Column(String(200), nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
