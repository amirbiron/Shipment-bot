"""
Station Service - ניהול תחנות, סדרנים, ארנק תחנה ורשימה שחורה

שירות מרכזי לכל הלוגיקה העסקית הקשורה לתחנות משלוחים [שלב 3].
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.station import Station
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_wallet import StationWallet
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType
from app.db.models.station_blacklist import StationBlacklist
from app.db.models.manual_charge import ManualCharge
from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.user import User
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator
from app.core.exceptions import ValidationException
from html import escape

logger = get_logger(__name__)


class StationService:
    """שירות ניהול תחנות משלוחים"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ==================== ניהול תחנה ====================

    async def create_station(self, name: str, owner_id: int) -> Station:
        """
        יצירת תחנה חדשה עם ארנק.

        שימוש ב-flush בלבד — הקוראים אחראים על commit
        כדי לאפשר אטומיות עם פעולות נוספות (למשל עדכון תפקיד).
        """
        station = Station(name=name, owner_id=owner_id)
        self.db.add(station)
        await self.db.flush()

        # יצירת ארנק לתחנה
        wallet = StationWallet(station_id=station.id)
        self.db.add(wallet)
        await self.db.flush()

        logger.info(
            "Station created",
            extra_data={"station_id": station.id, "owner_id": owner_id}
        )
        return station

    async def get_station(self, station_id: int) -> Optional[Station]:
        """קבלת תחנה פעילה לפי מזהה"""
        result = await self.db.execute(
            select(Station).where(
                Station.id == station_id,
                Station.is_active == True  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    async def get_station_by_owner(self, owner_id: int) -> Optional[Station]:
        """קבלת תחנה לפי בעל התחנה (מחזיר את הראשונה אם יש יותר מאחת)"""
        result = await self.db.execute(
            select(Station).where(
                Station.owner_id == owner_id,
                Station.is_active == True  # noqa: E712
            ).limit(1)
        )
        return result.scalar_one_or_none()

    # ==================== ניהול סדרנים [3.3] ====================

    async def add_dispatcher(
        self,
        station_id: int,
        phone_number: str
    ) -> tuple[bool, str]:
        """
        הוספת סדרן לתחנה לפי מספר טלפון.

        מחזיר (success, message).
        """
        # ולידציה ונרמול מספר טלפון
        if not PhoneNumberValidator.validate(phone_number):
            return False, "מספר טלפון לא תקין."

        normalized = PhoneNumberValidator.normalize(phone_number)

        # חיפוש המשתמש לפי מספר טלפון
        result = await self.db.execute(
            select(User).where(User.phone_number == normalized)
        )
        user = result.scalar_one_or_none()

        if not user:
            return False, "משתמש לא נמצא עם מספר הטלפון הזה."

        # בדיקה שהמשתמש לא כבר סדרן בתחנה הזו
        existing = await self.db.execute(
            select(StationDispatcher).where(
                StationDispatcher.station_id == station_id,
                StationDispatcher.user_id == user.id,
            )
        )
        existing_dispatcher = existing.scalar_one_or_none()
        if existing_dispatcher:
            if existing_dispatcher.is_active:
                return False, "המשתמש כבר סדרן בתחנה הזו."
            # הפעלה מחדש של סדרן שהוסר בעבר
            existing_dispatcher.is_active = True
        else:
            dispatcher = StationDispatcher(
                station_id=station_id,
                user_id=user.id,
            )
            self.db.add(dispatcher)

        await self.db.commit()

        logger.info(
            "Dispatcher added to station",
            extra_data={
                "station_id": station_id,
                "user_id": user.id,
                "phone": PhoneNumberValidator.mask(normalized),
            }
        )
        return True, f"הסדרן {escape(user.name or 'לא ידוע')} נוסף בהצלחה לתחנה."

    async def remove_dispatcher(
        self,
        station_id: int,
        user_id: int
    ) -> tuple[bool, str]:
        """הסרת סדרן מתחנה"""
        result = await self.db.execute(
            select(StationDispatcher).where(
                StationDispatcher.station_id == station_id,
                StationDispatcher.user_id == user_id,
            )
        )
        dispatcher = result.scalar_one_or_none()

        if not dispatcher:
            return False, "הסדרן לא נמצא בתחנה."

        dispatcher.is_active = False
        await self.db.commit()

        logger.info(
            "Dispatcher removed from station",
            extra_data={"station_id": station_id, "user_id": user_id}
        )
        return True, "הסדרן הוסר בהצלחה מהתחנה."

    async def get_dispatchers(self, station_id: int) -> List[StationDispatcher]:
        """קבלת רשימת סדרנים פעילים בתחנה"""
        result = await self.db.execute(
            select(StationDispatcher).where(
                StationDispatcher.station_id == station_id,
                StationDispatcher.is_active == True  # noqa: E712
            )
        )
        return list(result.scalars().all())

    async def get_dispatcher_station(self, user_id: int) -> Optional[Station]:
        """קבלת התחנה שהסדרן משויך אליה (מחזיר את הראשונה אם יש כמה)"""
        result = await self.db.execute(
            select(StationDispatcher).where(
                StationDispatcher.user_id == user_id,
                StationDispatcher.is_active == True  # noqa: E712
            ).limit(1)
        )
        dispatcher = result.scalar_one_or_none()
        if not dispatcher:
            return None

        return await self.get_station(dispatcher.station_id)

    async def is_dispatcher(self, user_id: int) -> bool:
        """בדיקה אם המשתמש הוא סדרן פעיל בתחנה פעילה"""
        result = await self.db.execute(
            select(StationDispatcher).join(
                Station, StationDispatcher.station_id == Station.id
            ).where(
                StationDispatcher.user_id == user_id,
                StationDispatcher.is_active == True,  # noqa: E712
                Station.is_active == True,  # noqa: E712
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def is_dispatcher_of_station(
        self, user_id: int, station_id: int
    ) -> bool:
        """בדיקה אם המשתמש הוא סדרן פעיל בתחנה ספציפית"""
        result = await self.db.execute(
            select(StationDispatcher).join(
                Station, StationDispatcher.station_id == Station.id
            ).where(
                StationDispatcher.user_id == user_id,
                StationDispatcher.station_id == station_id,
                StationDispatcher.is_active == True,  # noqa: E712
                Station.is_active == True,  # noqa: E712
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    # ==================== משלוחי תחנה [3.2] ====================

    async def get_station_active_deliveries(
        self, station_id: int
    ) -> List[Delivery]:
        """קבלת משלוחים פעילים של תחנה"""
        result = await self.db.execute(
            select(Delivery).where(
                Delivery.station_id == station_id,
                Delivery.status.in_([
                    DeliveryStatus.OPEN,
                    DeliveryStatus.PENDING_APPROVAL,  # שלב 4
                    DeliveryStatus.CAPTURED,
                    DeliveryStatus.IN_PROGRESS,
                ])
            ).order_by(Delivery.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_station_delivery_history(
        self, station_id: int, limit: int = 20
    ) -> List[Delivery]:
        """קבלת היסטוריית משלוחים של תחנה"""
        result = await self.db.execute(
            select(Delivery).where(
                Delivery.station_id == station_id,
                Delivery.status.in_([
                    DeliveryStatus.DELIVERED,
                    DeliveryStatus.CANCELLED,
                ])
            ).order_by(Delivery.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    # ==================== חיוב ידני [3.2] ====================

    @staticmethod
    def _normalize_driver_name(name: str) -> str:
        """נרמול שם נהג - הסרת רווחים מיותרים לעקביות בדוח גבייה"""
        import re as _re
        return _re.sub(r"\s+", " ", name.strip())

    async def create_manual_charge(
        self,
        station_id: int,
        dispatcher_id: int,
        driver_name: str,
        amount: float,
        description: str = ""
    ) -> ManualCharge:
        """יצירת חיוב ידני ע"י סדרן"""
        if amount <= 0:
            raise ValidationException("סכום החיוב חייב להיות חיובי", field="amount")

        normalized_name = self._normalize_driver_name(driver_name)
        charge = ManualCharge(
            station_id=station_id,
            dispatcher_id=dispatcher_id,
            driver_name=normalized_name,
            amount=amount,
            description=description,
        )
        self.db.add(charge)

        # עדכון ארנק התחנה - נעילת שורה למניעת race condition
        wallet = await self._get_or_create_station_wallet(station_id, for_update=True)
        wallet.balance += amount
        wallet.updated_at = datetime.utcnow()

        # רישום בלדג'ר
        ledger_entry = StationLedger(
            station_id=station_id,
            entry_type=StationLedgerEntryType.MANUAL_CHARGE,
            amount=amount,
            balance_after=wallet.balance,
            description=f"חיוב ידני: {normalized_name} - {description}",
        )
        self.db.add(ledger_entry)

        await self.db.commit()
        await self.db.refresh(charge)

        logger.info(
            "Manual charge created",
            extra_data={
                "station_id": station_id,
                "dispatcher_id": dispatcher_id,
                "amount": amount,
            }
        )
        return charge

    # ==================== ארנק תחנה [3.3] ====================

    async def _get_or_create_station_wallet(
        self, station_id: int, for_update: bool = False
    ) -> StationWallet:
        """קבלה או יצירה של ארנק תחנה"""
        query = select(StationWallet).where(
            StationWallet.station_id == station_id
        )
        if for_update:
            query = query.with_for_update()

        result = await self.db.execute(query)
        wallet = result.scalar_one_or_none()

        if not wallet:
            wallet = StationWallet(station_id=station_id)
            self.db.add(wallet)
            await self.db.flush()

        return wallet

    async def get_station_wallet(
        self, station_id: int
    ) -> StationWallet:
        """קבלת ארנק תחנה"""
        return await self._get_or_create_station_wallet(station_id)

    async def credit_station_commission(
        self,
        station_id: int,
        delivery_id: int,
        fee: float
    ) -> None:
        """זיכוי עמלת תחנה (10% מהמשלוח)"""
        if fee <= 0:
            raise ValidationException("עמלת משלוח חייבת להיות חיובית", field="fee")

        wallet = await self._get_or_create_station_wallet(station_id, for_update=True)
        commission = fee * wallet.commission_rate
        wallet.balance += commission
        wallet.updated_at = datetime.utcnow()

        ledger_entry = StationLedger(
            station_id=station_id,
            delivery_id=delivery_id,
            entry_type=StationLedgerEntryType.COMMISSION_CREDIT,
            amount=commission,
            balance_after=wallet.balance,
            description=f"עמלה ממשלוח #{delivery_id}",
        )
        self.db.add(ledger_entry)
        await self.db.commit()

    async def get_station_ledger(
        self, station_id: int, limit: int = 20
    ) -> List[StationLedger]:
        """קבלת היסטוריית תנועות ארנק תחנה"""
        result = await self.db.execute(
            select(StationLedger).where(
                StationLedger.station_id == station_id
            ).order_by(StationLedger.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    # ==================== רשימה שחורה [3.3] ====================

    async def add_to_blacklist(
        self,
        station_id: int,
        phone_number: str,
        reason: str = ""
    ) -> tuple[bool, str]:
        """הוספת נהג לרשימה שחורה של תחנה"""
        if not PhoneNumberValidator.validate(phone_number):
            return False, "מספר טלפון לא תקין."

        normalized = PhoneNumberValidator.normalize(phone_number)

        # חיפוש המשתמש
        result = await self.db.execute(
            select(User).where(User.phone_number == normalized)
        )
        user = result.scalar_one_or_none()

        if not user:
            return False, "משתמש לא נמצא."

        # בדיקה אם כבר חסום
        existing = await self.db.execute(
            select(StationBlacklist).where(
                StationBlacklist.station_id == station_id,
                StationBlacklist.courier_id == user.id,
            )
        )
        if existing.scalar_one_or_none():
            return False, "הנהג כבר ברשימה השחורה של התחנה."

        entry = StationBlacklist(
            station_id=station_id,
            courier_id=user.id,
            reason=reason,
        )
        self.db.add(entry)
        await self.db.commit()

        logger.info(
            "Driver added to station blacklist",
            extra_data={
                "station_id": station_id,
                "courier_id": user.id,
                "phone": PhoneNumberValidator.mask(normalized),
            }
        )
        return True, f"הנהג {escape(user.name or 'לא ידוע')} נוסף לרשימה השחורה."

    async def remove_from_blacklist(
        self,
        station_id: int,
        courier_id: int
    ) -> tuple[bool, str]:
        """הסרת נהג מרשימה שחורה של תחנה"""
        result = await self.db.execute(
            select(StationBlacklist).where(
                StationBlacklist.station_id == station_id,
                StationBlacklist.courier_id == courier_id,
            )
        )
        entry = result.scalar_one_or_none()

        if not entry:
            return False, "הנהג לא נמצא ברשימה השחורה."

        await self.db.delete(entry)
        await self.db.commit()

        return True, "הנהג הוסר מהרשימה השחורה."

    async def get_blacklist(
        self, station_id: int
    ) -> List[StationBlacklist]:
        """קבלת רשימה שחורה של תחנה"""
        result = await self.db.execute(
            select(StationBlacklist).where(
                StationBlacklist.station_id == station_id
            )
        )
        return list(result.scalars().all())

    async def is_blacklisted(
        self, station_id: int, courier_id: int
    ) -> bool:
        """בדיקה אם נהג חסום בתחנה"""
        result = await self.db.execute(
            select(StationBlacklist).where(
                StationBlacklist.station_id == station_id,
                StationBlacklist.courier_id == courier_id,
            )
        )
        return result.scalar_one_or_none() is not None

    # ==================== שלב 4: הגדרות קבוצות ====================

    async def update_station_groups(
        self,
        station_id: int,
        public_group_chat_id: str | None = None,
        public_group_platform: str | None = None,
        private_group_chat_id: str | None = None,
        private_group_platform: str | None = None,
    ) -> tuple[bool, str]:
        """עדכון מזהי קבוצות של תחנה"""
        station = await self.get_station(station_id)
        if not station:
            return False, "התחנה לא נמצאה."

        if public_group_chat_id is not None:
            station.public_group_chat_id = public_group_chat_id
            station.public_group_platform = public_group_platform or "telegram"
        if private_group_chat_id is not None:
            station.private_group_chat_id = private_group_chat_id
            station.private_group_platform = private_group_platform or "telegram"

        await self.db.commit()

        logger.info(
            "Station groups updated",
            extra_data={
                "station_id": station_id,
                "public_group": public_group_chat_id,
                "private_group": private_group_chat_id,
            }
        )
        return True, "✅ הגדרות הקבוצה עודכנו בהצלחה."

    # ==================== דוח גבייה [3.3] ====================

    @staticmethod
    def get_billing_cycle_start() -> datetime:
        """חישוב תחילת מחזור החיוב הנוכחי (28 לחודש)"""
        now = datetime.utcnow()
        if now.day >= 28:
            # אחרי ה-28 - מחזור התחיל ב-28 בחודש הנוכחי
            return now.replace(day=28, hour=0, minute=0, second=0, microsecond=0)
        # לפני ה-28 - מחזור התחיל ב-28 בחודש הקודם
        if now.month == 1:
            return now.replace(year=now.year - 1, month=12, day=28,
                               hour=0, minute=0, second=0, microsecond=0)
        return now.replace(month=now.month - 1, day=28,
                           hour=0, minute=0, second=0, microsecond=0)

    async def get_collection_report(
        self, station_id: int
    ) -> List[dict]:
        """
        דוח גבייה - רשימת נהגים שחייבים כסף לתחנה במחזור הנוכחי.

        מחזור חיוב: מה-28 בחודש הקודם עד ה-28 בחודש הנוכחי.
        """
        cycle_start = self.get_billing_cycle_start()

        # קבלת חיובים ממחזור החיוב הנוכחי בלבד
        result = await self.db.execute(
            select(ManualCharge).where(
                ManualCharge.station_id == station_id,
                ManualCharge.created_at >= cycle_start,
            ).order_by(ManualCharge.created_at.desc())
        )
        charges = list(result.scalars().all())

        # קיבוץ לפי שם נהג
        report: dict[str, float] = {}
        for charge in charges:
            if charge.driver_name not in report:
                report[charge.driver_name] = 0.0
            report[charge.driver_name] += charge.amount

        return [
            {"driver_name": name, "total_debt": total}
            for name, total in report.items()
            if total > 0
        ]

    async def get_collection_report_for_period(
        self, station_id: int, cycle_start: datetime, cycle_end: datetime,
    ) -> List[dict]:
        """
        דוח גבייה לתקופה מותאמת — רשימת נהגים שחייבים כסף לתחנה.

        מחזיר רשימה עם driver_name, total_debt, charge_count.
        """
        result = await self.db.execute(
            select(ManualCharge).where(
                ManualCharge.station_id == station_id,
                ManualCharge.created_at >= cycle_start,
                ManualCharge.created_at < cycle_end,
            ).order_by(ManualCharge.created_at.desc())
        )
        charges = list(result.scalars().all())

        # קיבוץ לפי שם נהג
        report: dict[str, dict] = {}
        for charge in charges:
            name = charge.driver_name
            if name not in report:
                report[name] = {"total_debt": 0.0, "charge_count": 0}
            report[name]["total_debt"] += charge.amount
            report[name]["charge_count"] += 1

        return [
            {"driver_name": name, "total_debt": data["total_debt"], "charge_count": data["charge_count"]}
            for name, data in report.items()
            if data["total_debt"] > 0
        ]
