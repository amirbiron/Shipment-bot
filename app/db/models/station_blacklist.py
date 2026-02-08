"""
Station Blacklist Model - רשימה שחורה ברמת תחנה

נהגים שלא שילמו חודשיים רצופים נחסמים מהתחנה הספציפית בלבד.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, DateTime, ForeignKey, String, UniqueConstraint

from app.db.database import Base


class StationBlacklist(Base):
    """נהג חסום ברמת תחנה ספציפית"""

    __tablename__ = "station_blacklist"

    id = Column(Integer, primary_key=True, index=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False, index=True)
    courier_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)

    reason = Column(String(500), nullable=True)
    consecutive_unpaid_months = Column(Integer, default=2)

    blocked_at = Column(DateTime, default=datetime.utcnow)

    # נהג יכול להיות חסום פעם אחת בכל תחנה
    __table_args__ = (
        UniqueConstraint('station_id', 'courier_id', name='uq_station_blacklist'),
    )
