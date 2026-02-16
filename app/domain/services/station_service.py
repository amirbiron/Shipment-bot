"""
Station Service - ניהול תחנות, סדרנים, ארנק תחנה ורשימה שחורה

שירות מרכזי לכל הלוגיקה העסקית הקשורה לתחנות משלוחים [שלב 3].
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_dispatcher import StationDispatcher
from app.db.models.station_wallet import StationWallet
from app.db.models.station_ledger import StationLedger, StationLedgerEntryType
from app.db.models.station_blacklist import StationBlacklist
from app.db.models.manual_charge import ManualCharge
from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.user import User, UserRole
from app.db.models.courier_wallet import CourierWallet
from app.db.models.audit_log import AuditLog, AuditActionType
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator, TextSanitizer, OperatingHoursValidator, ServiceAreasValidator
from app.core.exceptions import ValidationException
from html import escape

logger = get_logger(__name__)


class StationService:
    """שירות ניהול תחנות משלוחים"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ==================== לוג ביקורת ====================

    async def _record_audit(
        self,
        station_id: int,
        actor_user_id: int,
        action: AuditActionType,
        target_user_id: int | None = None,
        details: dict | None = None,
    ) -> None:
        """רישום פעולה מנהלתית בלוג ביקורת — באותה טרנזקציה עם הפעולה.

        חובה לקרוא לפני commit() כדי להבטיח אטומיות.
        """
        entry = AuditLog(
            station_id=station_id,
            actor_user_id=actor_user_id,
            action=action,
            target_user_id=target_user_id,
            details=details,
        )
        self.db.add(entry)

    async def _verify_station_owner(
        self,
        station_id: int,
        actor_user_id: int | None,
    ) -> None:
        """בדיקת הרשאה — רק בעלים פעיל יכול לבצע פעולות מנהלתיות על הגדרות תחנה.

        דילוג כש-actor_user_id=None (תאימות לאחור עם קריאות פנימיות).
        """
        if actor_user_id is None:
            return
        if not await self.is_owner_of_station(actor_user_id, station_id):
            raise ValidationException("אין הרשאה לבצע פעולה זו — רק בעלים פעיל בתחנה.")

    async def get_audit_logs(
        self,
        station_id: int,
        action: AuditActionType | None = None,
        actor_user_id: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuditLog], int]:
        """קבלת לוג ביקורת עם סינון ו-pagination.

        Args:
            station_id: מזהה התחנה
            action: סינון לפי סוג פעולה
            actor_user_id: סינון לפי משתמש מבצע
            date_from: מתאריך
            date_to: עד תאריך
            page: עמוד (1-based)
            page_size: גודל עמוד

        Returns:
            (רשימת רשומות, סה"כ רשומות)
        """
        from sqlalchemy import func

        base_where = [AuditLog.station_id == station_id]

        if action is not None:
            base_where.append(AuditLog.action == action)
        if actor_user_id is not None:
            base_where.append(AuditLog.actor_user_id == actor_user_id)
        if date_from is not None:
            base_where.append(AuditLog.created_at >= date_from)
        if date_to is not None:
            base_where.append(AuditLog.created_at <= date_to)

        # ספירה
        count_result = await self.db.execute(
            select(func.count(AuditLog.id)).where(*base_where)
        )
        total = count_result.scalar() or 0

        # שליפה
        offset = (page - 1) * page_size
        result = await self.db.execute(
            select(AuditLog)
            .where(*base_where)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        entries = list(result.scalars().all())

        return entries, total

    async def get_or_create_user_by_phone(
        self,
        normalized_phone: str,
        context: str = "",
    ) -> User:
        """חיפוש משתמש לפי מספר טלפון מנורמל — אם לא קיים, יוצרים אוטומטית.

        משתמש ב-savepoint + IntegrityError fallback למניעת race condition
        כשבקשות מקבילות מנסות ליצור אותו משתמש.
        הפלטפורמה תתעדכן בפעם הראשונה שהמשתמש יתחבר דרך הבוט.
        """
        result = await self.db.execute(
            select(User).where(User.phone_number == normalized_phone)
        )
        user = result.scalar_one_or_none()
        if user:
            return user

        try:
            async with self.db.begin_nested():
                user = User(
                    phone_number=normalized_phone,
                    platform="telegram",
                    role=UserRole.SENDER,
                )
                self.db.add(user)
        except IntegrityError:
            # race condition — משתמש נוצר במקביל עם אותו phone_number
            logger.info(
                "IntegrityError ביצירת משתמש — כנראה נוצר במקביל, מנסה למצוא",
                extra_data={"phone": PhoneNumberValidator.mask(normalized_phone)},
            )
            result = await self.db.execute(
                select(User).where(User.phone_number == normalized_phone)
            )
            user = result.scalar_one_or_none()
            if not user:
                raise ValueError(
                    f"לא ניתן ליצור או למצוא משתמש: {PhoneNumberValidator.mask(normalized_phone)}"
                )
            return user

        logger.info(
            f"יצירת משתמש אוטומטית {context}",
            extra_data={
                "user_id": user.id,
                "phone": PhoneNumberValidator.mask(normalized_phone),
            }
        )
        return user

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

        # יצירת רשומת בעלים בטבלת junction — מבטיח עקביות עם station_owners
        owner_record = StationOwner(station_id=station.id, user_id=owner_id)
        self.db.add(owner_record)

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
        """קבלת תחנה לפי בעלים — בודק קודם בטבלת station_owners, אח"כ fallback ל-owner_id"""
        # בדיקה ראשונה: טבלת station_owners (ריבוי בעלים)
        result = await self.db.execute(
            select(Station).join(
                StationOwner, StationOwner.station_id == Station.id
            ).where(
                StationOwner.user_id == owner_id,
                StationOwner.is_active == True,  # noqa: E712
                Station.is_active == True,  # noqa: E712
            ).limit(1)
        )
        station = result.scalar_one_or_none()
        if station:
            return station

        # fallback: שדה owner_id ישן (תאימות לאחור עד שכל הנתונים יעברו)
        result = await self.db.execute(
            select(Station).where(
                Station.owner_id == owner_id,
                Station.is_active == True  # noqa: E712
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_stations_by_owner(self, owner_id: int) -> List[Station]:
        """קבלת כל התחנות שהמשתמש בעלים בהן — ממזג junction table ו-owner_id fallback"""
        # תחנות מטבלת junction
        result = await self.db.execute(
            select(Station).join(
                StationOwner, StationOwner.station_id == Station.id
            ).where(
                StationOwner.user_id == owner_id,
                StationOwner.is_active == True,  # noqa: E712
                Station.is_active == True,  # noqa: E712
            )
        )
        stations = list(result.scalars().all())
        junction_ids = {s.id for s in stations}

        # fallback: תחנות עם owner_id ישן שלא נמצאו דרך junction
        # (תחנות שלא עברו מיגרציה לטבלת station_owners)
        result = await self.db.execute(
            select(Station).where(
                Station.owner_id == owner_id,
                Station.is_active == True  # noqa: E712
            )
        )
        for s in result.scalars().all():
            if s.id not in junction_ids:
                stations.append(s)

        return stations

    async def is_owner_of_station(self, user_id: int, station_id: int) -> bool:
        """בדיקה אם המשתמש הוא בעלים פעיל בתחנה ספציפית"""
        # בדיקה בטבלת station_owners
        result = await self.db.execute(
            select(StationOwner).join(
                Station, StationOwner.station_id == Station.id
            ).where(
                StationOwner.user_id == user_id,
                StationOwner.station_id == station_id,
                StationOwner.is_active == True,  # noqa: E712
                Station.is_active == True,  # noqa: E712
            ).limit(1)
        )
        if result.scalar_one_or_none() is not None:
            return True

        # fallback: שדה owner_id ישן
        result = await self.db.execute(
            select(Station).where(
                Station.id == station_id,
                Station.owner_id == user_id,
                Station.is_active == True  # noqa: E712
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    # ==================== ניהול בעלים ====================

    async def add_owner(
        self,
        station_id: int,
        phone_number: str,
        actor_user_id: int | None = None,
    ) -> tuple[bool, str]:
        """הוספת בעלים לתחנה לפי מספר טלפון"""
        if not PhoneNumberValidator.validate(phone_number):
            return False, "מספר טלפון לא תקין."

        normalized = PhoneNumberValidator.normalize(phone_number)

        # חיפוש המשתמש — אם לא קיים, יוצרים אותו אוטומטית
        user = await self.get_or_create_user_by_phone(normalized, context="בעת הוספת בעלים")

        # בדיקה שלא כבר בעלים בתחנה
        existing = await self.db.execute(
            select(StationOwner).where(
                StationOwner.station_id == station_id,
                StationOwner.user_id == user.id,
            )
        )
        existing_owner = existing.scalar_one_or_none()
        if existing_owner:
            if existing_owner.is_active:
                return False, "המשתמש כבר בעלים בתחנה הזו."
            # הפעלה מחדש של בעלים שהוסר בעבר
            existing_owner.is_active = True
        else:
            owner_record = StationOwner(
                station_id=station_id,
                user_id=user.id,
            )
            self.db.add(owner_record)

        # עדכון תפקיד המשתמש ל-STATION_OWNER אם צריך
        if user.role != UserRole.STATION_OWNER:
            user.role = UserRole.STATION_OWNER

        # רישום בלוג ביקורת
        if actor_user_id is not None:
            await self._record_audit(
                station_id=station_id,
                actor_user_id=actor_user_id,
                action=AuditActionType.OWNER_ADDED,
                target_user_id=user.id,
                details={
                    "target_phone": PhoneNumberValidator.mask(normalized),
                    "target_name": user.name or user.full_name or "לא ידוע",
                },
            )

        await self.db.commit()

        logger.info(
            "Owner added to station",
            extra_data={
                "station_id": station_id,
                "user_id": user.id,
                "phone": PhoneNumberValidator.mask(normalized),
            }
        )
        return True, f"הבעלים {escape(user.name or user.full_name or 'לא ידוע')} נוסף בהצלחה לתחנה."

    async def remove_owner(
        self,
        station_id: int,
        user_id: int,
        actor_user_id: int | None = None,
    ) -> tuple[bool, str]:
        """הסרת בעלים מתחנה — לא ניתן להסיר את הבעלים האחרון"""
        # בדיקה שיש יותר מבעלים אחד
        result = await self.db.execute(
            select(StationOwner).where(
                StationOwner.station_id == station_id,
                StationOwner.is_active == True,  # noqa: E712
            )
        )
        active_owners = list(result.scalars().all())

        if len(active_owners) <= 1:
            return False, "לא ניתן להסיר את הבעלים האחרון של התחנה."

        # מציאת הרשומה להסרה
        target = None
        for o in active_owners:
            if o.user_id == user_id:
                target = o
                break

        if not target:
            return False, "הבעלים לא נמצא בתחנה."

        target.is_active = False

        # אם הבעלים שמוסר הוא ה-owner_id של התחנה — מעדכנים לבעלים פעיל אחר
        # מונע את ה-fallback של owner_id מלהחזיר גישה לבעלים שהוסר
        station = await self.get_station(station_id)
        if station and station.owner_id == user_id:
            remaining = [o for o in active_owners if o.user_id != user_id]
            if remaining:
                station.owner_id = remaining[0].user_id

        # אם המשתמש כבר לא בעלים של אף תחנה — מחזירים את התפקיד המקורי
        # משתמש ב-get_stations_by_owner שממזג junction table ו-owner_id fallback
        remaining_stations = await self.get_stations_by_owner(user_id)
        if not remaining_stations:
            user_result = await self.db.execute(
                select(User).where(User.id == user_id)
            )
            user = user_result.scalar_one_or_none()
            if user and user.role == UserRole.STATION_OWNER:
                # בדיקה אם היה שליח (יש לו ארנק שליח)
                wallet_result = await self.db.execute(
                    select(CourierWallet).where(
                        CourierWallet.courier_id == user_id
                    ).limit(1)
                )
                if wallet_result.scalar_one_or_none() is not None:
                    user.role = UserRole.COURIER
                else:
                    user.role = UserRole.SENDER
                logger.info(
                    "Reverted user role after station ownership removal",
                    extra_data={
                        "user_id": user_id,
                        "new_role": user.role.value,
                    }
                )

        # רישום בלוג ביקורת
        if actor_user_id is not None:
            await self._record_audit(
                station_id=station_id,
                actor_user_id=actor_user_id,
                action=AuditActionType.OWNER_REMOVED,
                target_user_id=user_id,
            )

        await self.db.commit()

        logger.info(
            "Owner removed from station",
            extra_data={"station_id": station_id, "user_id": user_id}
        )
        return True, "הבעלים הוסר בהצלחה מהתחנה."

    async def get_owners(self, station_id: int) -> List[StationOwner]:
        """קבלת רשימת בעלים פעילים בתחנה — כולל מיגרציה אוטומטית מ-owner_id ישן"""
        result = await self.db.execute(
            select(StationOwner).where(
                StationOwner.station_id == station_id,
                StationOwner.is_active == True  # noqa: E712
            )
        )
        owners = list(result.scalars().all())
        junction_user_ids = {o.user_id for o in owners}

        # מיגרציה אוטומטית: בעלים מ-Station.owner_id שלא קיים ב-junction (תחנה לפני מיגרציה)
        # יוצרים רשומת StationOwner אמיתית כדי שפעולות כמו remove_owner יעבדו נכון
        station = await self.get_station(station_id)
        if station and station.owner_id and station.owner_id not in junction_user_ids:
            # בדיקה אם יש רשומה לא פעילה — הפעלה מחדש במקום הכנסה חדשה
            # (מונע התנגשות עם UniqueConstraint על station_id + user_id)
            existing_result = await self.db.execute(
                select(StationOwner).where(
                    StationOwner.station_id == station_id,
                    StationOwner.user_id == station.owner_id,
                    StationOwner.is_active == False,  # noqa: E712
                )
            )
            inactive_owner = existing_result.scalar_one_or_none()
            if inactive_owner:
                inactive_owner.is_active = True
                legacy_owner = inactive_owner
            else:
                legacy_owner = StationOwner(
                    station_id=station_id,
                    user_id=station.owner_id,
                    is_active=True,
                )
                self.db.add(legacy_owner)
            await self.db.flush()
            owners.append(legacy_owner)
            logger.info(
                "מיגרציה אוטומטית של בעלים מ-owner_id לטבלת junction",
                extra_data={"station_id": station_id, "user_id": station.owner_id},
            )

        return owners

    # ==================== ניהול סדרנים [3.3] ====================

    async def add_dispatcher(
        self,
        station_id: int,
        phone_number: str,
        actor_user_id: int | None = None,
    ) -> tuple[bool, str]:
        """
        הוספת סדרן לתחנה לפי מספר טלפון.

        מחזיר (success, message).
        """
        # ולידציה ונרמול מספר טלפון
        if not PhoneNumberValidator.validate(phone_number):
            return False, "מספר טלפון לא תקין."

        normalized = PhoneNumberValidator.normalize(phone_number)

        # חיפוש המשתמש לפי מספר טלפון — אם לא קיים, יוצרים אותו אוטומטית
        user = await self.get_or_create_user_by_phone(normalized, context="בעת הוספת סדרן")

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

        # רישום בלוג ביקורת
        if actor_user_id is not None:
            await self._record_audit(
                station_id=station_id,
                actor_user_id=actor_user_id,
                action=AuditActionType.DISPATCHER_ADDED,
                target_user_id=user.id,
                details={
                    "target_phone": PhoneNumberValidator.mask(normalized),
                    "target_name": user.name or "לא ידוע",
                },
            )

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
        user_id: int,
        actor_user_id: int | None = None,
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

        # רישום בלוג ביקורת
        if actor_user_id is not None:
            await self._record_audit(
                station_id=station_id,
                actor_user_id=actor_user_id,
                action=AuditActionType.DISPATCHER_REMOVED,
                target_user_id=user_id,
            )

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

    async def _resolve_courier_id_by_name(self, driver_name: str) -> int | None:
        """ניסיון best-effort לזהות שליח לפי שם — מחזיר courier_id אם נמצא שליח יחיד תואם."""
        from sqlalchemy import func, or_
        from app.db.models.user import UserRole
        normalized = self._normalize_driver_name(driver_name).lower()
        # חיפוש התאמה מדויקת (case-insensitive) ברמת SQL — מונע full table scan
        result = await self.db.execute(
            select(User).where(
                User.role == UserRole.COURIER,
                User.is_active == True,  # noqa: E712
                or_(
                    func.lower(User.full_name) == normalized,
                    func.lower(User.name) == normalized,
                ),
            )
        )
        matches = list(result.scalars().all())
        if len(matches) == 1:
            return matches[0].id
        return None

    async def create_manual_charge(
        self,
        station_id: int,
        dispatcher_id: int,
        driver_name: str,
        amount: float,
        description: str = "",
        courier_id: int | None = None
    ) -> ManualCharge:
        """יצירת חיוב ידני ע"י סדרן

        Args:
            courier_id: מזהה שליח - אם לא סופק, ינסה לזהות אוטומטית לפי שם
        """
        if amount <= 0:
            raise ValidationException("סכום החיוב חייב להיות חיובי", field="amount")

        normalized_name = self._normalize_driver_name(driver_name)

        # ניסיון best-effort לזהות שליח אם לא סופק courier_id
        if courier_id is None:
            courier_id = await self._resolve_courier_id_by_name(normalized_name)
            if courier_id:
                logger.info(
                    "זיהוי אוטומטי של שליח לחיוב ידני",
                    extra_data={"courier_id": courier_id, "driver_name": normalized_name}
                )

        amount_decimal = Decimal(str(amount))
        charge = ManualCharge(
            station_id=station_id,
            dispatcher_id=dispatcher_id,
            driver_name=normalized_name,
            amount=amount_decimal,
            description=description,
            courier_id=courier_id,
        )
        self.db.add(charge)

        # עדכון ארנק התחנה - נעילת שורה למניעת race condition
        wallet = await self._get_or_create_station_wallet(station_id, for_update=True)
        wallet.balance += amount_decimal
        wallet.updated_at = datetime.utcnow()

        # רישום בלדג'ר
        ledger_entry = StationLedger(
            station_id=station_id,
            entry_type=StationLedgerEntryType.MANUAL_CHARGE,
            amount=amount_decimal,
            balance_after=wallet.balance,
            description=f"חיוב ידני: {normalized_name} - {description}",
        )
        self.db.add(ledger_entry)

        # רישום בלוג ביקורת — dispatcher_id הוא הפועל
        await self._record_audit(
            station_id=station_id,
            actor_user_id=dispatcher_id,
            action=AuditActionType.MANUAL_CHARGE_CREATED,
            target_user_id=courier_id,
            details={
                "driver_name": normalized_name,
                "amount": float(amount_decimal),
                "description": TextSanitizer.sanitize(description, max_length=500) if description else "",
            },
        )

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

    # גבולות אחוז עמלה — מקור אמת יחיד לכל השכבות
    COMMISSION_MIN_PCT = 6
    COMMISSION_MAX_PCT = 12
    MIN_COMMISSION_RATE = Decimal(str(COMMISSION_MIN_PCT)) / Decimal("100")
    MAX_COMMISSION_RATE = Decimal(str(COMMISSION_MAX_PCT)) / Decimal("100")

    async def update_commission_rate(
        self,
        station_id: int,
        new_rate: float,
        actor_user_id: int | None = None,
    ) -> tuple[bool, str]:
        """עדכון אחוז עמלה של תחנה.

        Args:
            station_id: מזהה התחנה
            new_rate: אחוז העמלה כערך עשרוני (0.06–0.12)
            actor_user_id: מזהה המשתמש שביצע את העדכון (לצורכי audit)

        Returns:
            (success, message)
        """
        await self._verify_station_owner(station_id, actor_user_id)

        rate = Decimal(str(new_rate))

        if rate < self.MIN_COMMISSION_RATE or rate > self.MAX_COMMISSION_RATE:
            pct_min = int(self.MIN_COMMISSION_RATE * 100)
            pct_max = int(self.MAX_COMMISSION_RATE * 100)
            return False, f"אחוז עמלה חייב להיות בין {pct_min}% ל-{pct_max}%."

        wallet = await self._get_or_create_station_wallet(station_id, for_update=True)
        old_rate = wallet.commission_rate
        wallet.commission_rate = rate
        wallet.updated_at = datetime.utcnow()

        # רישום בלוג ביקורת
        if actor_user_id is not None:
            await self._record_audit(
                station_id=station_id,
                actor_user_id=actor_user_id,
                action=AuditActionType.COMMISSION_RATE_UPDATED,
                details={"changes": {
                    "commission_rate": {
                        "old_value": f"{int(old_rate * 100)}%" if old_rate is not None else None,
                        "new_value": f"{int(rate * 100)}%",
                    },
                }},
            )

        await self.db.commit()

        logger.info(
            "Commission rate updated",
            extra_data={
                "station_id": station_id,
                "actor_user_id": actor_user_id,
                "old_rate": float(old_rate) if old_rate is not None else None,
                "new_rate": float(rate),
            }
        )
        return True, f"אחוז העמלה עודכן בהצלחה ל-{int(rate * 100)}%."

    async def credit_station_commission(
        self,
        station_id: int,
        delivery_id: int,
        fee: float,
        auto_commit: bool = True
    ) -> None:
        """זיכוי עמלת תחנה (10% מהמשלוח)

        Args:
            station_id: מזהה התחנה
            delivery_id: מזהה המשלוח
            fee: עמלת המשלוח
            auto_commit: האם לבצע commit - False כשהמתקשר מנהל את הטרנזקציה
        """
        if fee <= 0:
            raise ValidationException("עמלת משלוח חייבת להיות חיובית", field="fee")

        wallet = await self._get_or_create_station_wallet(station_id, for_update=True)
        commission = Decimal(str(fee)) * wallet.commission_rate
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
        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

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
        reason: str = "",
        actor_user_id: int | None = None,
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

        # רישום בלוג ביקורת
        if actor_user_id is not None:
            await self._record_audit(
                station_id=station_id,
                actor_user_id=actor_user_id,
                action=AuditActionType.BLACKLIST_ADDED,
                target_user_id=user.id,
                details={
                    "target_phone": PhoneNumberValidator.mask(normalized),
                    "reason": TextSanitizer.sanitize(reason, max_length=500) if reason else "",
                },
            )

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
        courier_id: int,
        actor_user_id: int | None = None,
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

        # רישום בלוג ביקורת
        if actor_user_id is not None:
            await self._record_audit(
                station_id=station_id,
                actor_user_id=actor_user_id,
                action=AuditActionType.BLACKLIST_REMOVED,
                target_user_id=courier_id,
            )

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

    # ==================== סעיף 8: הגדרות תחנה מורחבות ====================

    async def get_station_settings(
        self,
        station_id: int,
    ) -> dict:
        """קבלת הגדרות מורחבות של תחנה"""
        station = await self.get_station(station_id)
        if not station:
            return {}

        return {
            "name": station.name,
            "description": station.description,
            "operating_hours": station.operating_hours,
            "service_areas": station.service_areas,
            "logo_url": station.logo_url,
            "auto_block_enabled": station.auto_block_enabled,
            "auto_block_grace_months": station.auto_block_grace_months,
            "auto_block_min_debt": float(station.auto_block_min_debt),
        }

    async def update_station_settings(
        self,
        station_id: int,
        name: str | None = None,
        description: str | None = ...,  # sentinel — None מוחק, ... = לא שלחו
        operating_hours: dict | None = ...,
        service_areas: list | None = ...,
        logo_url: str | None = ...,
        actor_user_id: int | None = None,
    ) -> tuple[bool, str]:
        """עדכון הגדרות מורחבות של תחנה.

        שדות עם ערך ... (sentinel) לא יעודכנו.
        שדות עם ערך None ינוקו (יאופסו ל-null).

        Args:
            station_id: מזהה התחנה
            name: שם התחנה (2-200 תווים)
            description: תיאור התחנה (עד 500 תווים), None = מחיקה
            operating_hours: שעות פעילות בפורמט JSON, None = מחיקה
            service_areas: רשימת אזורי שירות, None = מחיקה
            logo_url: קישור ללוגו, None = מחיקה

        Returns:
            (success, message)
        """
        await self._verify_station_owner(station_id, actor_user_id)

        station = await self.get_station(station_id)
        if not station:
            return False, "התחנה לא נמצאה."

        # --- שלב 1: ולידציה וסניטציה — לפני כל שינוי על האובייקט ---
        # אוספים את כל העדכונים ב-dict, ומחילים רק אם הכל תקין.
        updates: dict[str, object] = {}

        # ולידציית שם
        if name is not None:
            sanitized_name = TextSanitizer.sanitize(name.strip(), max_length=200)
            if len(sanitized_name) < 2:
                return False, "שם התחנה חייב להכיל לפחות 2 תווים."
            is_safe, pattern = TextSanitizer.check_for_injection(sanitized_name)
            if not is_safe:
                return False, "שם התחנה מכיל תוכן לא תקין."
            updates["name"] = sanitized_name

        # ולידציית תיאור
        if description is not ...:
            if description is not None:
                sanitized_desc = TextSanitizer.sanitize(description.strip(), max_length=500)
                is_safe, pattern = TextSanitizer.check_for_injection(sanitized_desc)
                if not is_safe:
                    return False, "התיאור מכיל תוכן לא תקין."
                updates["description"] = sanitized_desc
            else:
                updates["description"] = None

        # ולידציית שעות פעילות
        if operating_hours is not ...:
            if operating_hours is not None:
                is_valid, error = OperatingHoursValidator.validate(operating_hours)
                if not is_valid:
                    return False, error
                updates["operating_hours"] = operating_hours
            else:
                updates["operating_hours"] = None

        # ולידציית אזורי שירות
        if service_areas is not ...:
            if service_areas is not None:
                is_valid, error = ServiceAreasValidator.validate(service_areas)
                if not is_valid:
                    return False, error
                updates["service_areas"] = ServiceAreasValidator.sanitize(service_areas)
            else:
                updates["service_areas"] = None

        # ולידציית לוגו
        if logo_url is not ...:
            if logo_url is not None:
                sanitized_url = TextSanitizer.sanitize(logo_url.strip(), max_length=500)
                is_safe, pattern = TextSanitizer.check_for_injection(sanitized_url)
                if not is_safe:
                    return False, "קישור הלוגו מכיל תוכן לא תקין."
                updates["logo_url"] = sanitized_url
            else:
                updates["logo_url"] = None

        # --- שלב 2: כל הולידציות עברו — שמירת ערכים ישנים ל-audit ---
        old_values: dict[str, object] = {}
        for field in updates:
            old_values[field] = getattr(station, field)

        # --- שלב 3: מחילים את העדכונים ---
        for field, value in updates.items():
            setattr(station, field, value)

        # רישום בלוג ביקורת
        if actor_user_id is not None and updates:
            changes: dict[str, dict[str, object]] = {}
            for field in updates:
                changes[field] = {
                    "old_value": str(old_values[field]) if old_values[field] is not None else None,
                    "new_value": str(updates[field]) if updates[field] is not None else None,
                }
            await self._record_audit(
                station_id=station_id,
                actor_user_id=actor_user_id,
                action=AuditActionType.STATION_SETTINGS_UPDATED,
                details={"changes": changes},
            )

        await self.db.commit()

        logger.info(
            "הגדרות תחנה עודכנו",
            extra_data={"station_id": station_id}
        )
        return True, "הגדרות התחנה עודכנו בהצלחה."

    # ==================== שלב 4: הגדרות קבוצות ====================

    async def update_station_groups(
        self,
        station_id: int,
        public_group_chat_id: str | None = None,
        public_group_platform: str | None = None,
        private_group_chat_id: str | None = None,
        private_group_platform: str | None = None,
        actor_user_id: int | None = None,
    ) -> tuple[bool, str]:
        """עדכון מזהי קבוצות של תחנה"""
        await self._verify_station_owner(station_id, actor_user_id)

        station = await self.get_station(station_id)
        if not station:
            return False, "התחנה לא נמצאה."

        # שמירת ערכים ישנים ל-audit
        old_public = station.public_group_chat_id
        old_private = station.private_group_chat_id

        if public_group_chat_id is not None:
            station.public_group_chat_id = public_group_chat_id
            station.public_group_platform = public_group_platform or "telegram"
        if private_group_chat_id is not None:
            station.private_group_chat_id = private_group_chat_id
            station.private_group_platform = private_group_platform or "telegram"

        # רישום בלוג ביקורת — רק אם בוצע שינוי בפועל
        if actor_user_id is not None:
            changes: dict[str, dict[str, object]] = {}
            if public_group_chat_id is not None:
                changes["public_group_chat_id"] = {"old_value": old_public, "new_value": public_group_chat_id}
            if private_group_chat_id is not None:
                changes["private_group_chat_id"] = {"old_value": old_private, "new_value": private_group_chat_id}
            if changes:
                await self._record_audit(
                    station_id=station_id,
                    actor_user_id=actor_user_id,
                    action=AuditActionType.GROUP_SETTINGS_UPDATED,
                    details={"changes": changes},
                )

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
        מדליגט ל-get_collection_report_for_period למניעת כפילות קוד.
        """
        cycle_start = self.get_billing_cycle_start()
        return await self.get_collection_report_for_period(station_id, cycle_start)

    async def get_collection_report_for_period(
        self, station_id: int, cycle_start: datetime, cycle_end: Optional[datetime] = None,
    ) -> List[dict]:
        """
        דוח גבייה לתקופה — רשימת נהגים שחייבים כסף לתחנה.

        מחזיר רשימה עם driver_name, total_debt, charge_count.
        אם cycle_end לא סופק — ללא גבול עליון.
        """
        query = select(ManualCharge).where(
            ManualCharge.station_id == station_id,
            ManualCharge.created_at >= cycle_start,
            ManualCharge.is_paid == False,  # noqa: E712 — שלב 5: סינון חיובים ששולמו
        )
        if cycle_end is not None:
            query = query.where(ManualCharge.created_at < cycle_end)
        query = query.order_by(ManualCharge.created_at.desc())

        result = await self.db.execute(query)
        charges = list(result.scalars().all())

        # קיבוץ לפי שם נהג
        report: dict[str, dict] = {}
        for charge in charges:
            name = charge.driver_name
            if name not in report:
                report[name] = {"total_debt": Decimal("0"), "charge_count": 0}
            report[name]["total_debt"] += charge.amount
            report[name]["charge_count"] += 1

        return [
            {"driver_name": name, "total_debt": data["total_debt"], "charge_count": data["charge_count"]}
            for name, data in report.items()
            if data["total_debt"] > 0
        ]

    # ==================== סעיף 10: הגדרות חסימה אוטומטית ====================

    async def update_auto_block_settings(
        self,
        station_id: int,
        auto_block_enabled: bool | None = None,
        auto_block_grace_months: int | None = None,
        auto_block_min_debt: float | None = None,
        actor_user_id: int | None = None,
    ) -> tuple[bool, str]:
        """עדכון הגדרות חסימה אוטומטית של תחנה.

        Args:
            station_id: מזהה התחנה
            auto_block_enabled: האם חסימה אוטומטית פעילה
            auto_block_grace_months: מספר חודשים רצופים לפני חסימה (1-12)
            auto_block_min_debt: סף חוב מינימלי לחסימה (0 ומעלה)

        Returns:
            (success, message)
        """
        await self._verify_station_owner(station_id, actor_user_id)

        station = await self.get_station(station_id)
        if not station:
            return False, "התחנה לא נמצאה."

        # --- שלב 1: ולידציה — לפני כל mutation על המודל ---
        if auto_block_grace_months is not None:
            if auto_block_grace_months < 1 or auto_block_grace_months > 12:
                return False, "תקופת חסד חייבת להיות בין 1 ל-12 חודשים."

        if auto_block_min_debt is not None:
            if auto_block_min_debt < 0:
                return False, "סף חוב מינימלי לא יכול להיות שלילי."

        # --- שלב 2: שמירת ערכים ישנים ל-audit ---
        old_values = {
            "auto_block_enabled": station.auto_block_enabled,
            "auto_block_grace_months": station.auto_block_grace_months,
            "auto_block_min_debt": float(station.auto_block_min_debt),
        }

        # --- שלב 3: מחילים את העדכונים ---
        if auto_block_enabled is not None:
            station.auto_block_enabled = auto_block_enabled
        if auto_block_grace_months is not None:
            station.auto_block_grace_months = auto_block_grace_months
        if auto_block_min_debt is not None:
            station.auto_block_min_debt = auto_block_min_debt

        # רישום בלוג ביקורת — רק אם בוצע שינוי בפועל
        if actor_user_id is not None:
            changes: dict[str, dict[str, object]] = {}
            new_values = {
                "auto_block_enabled": station.auto_block_enabled,
                "auto_block_grace_months": station.auto_block_grace_months,
                "auto_block_min_debt": float(station.auto_block_min_debt),
            }
            for field, old_val in old_values.items():
                if new_values[field] != old_val:
                    changes[field] = {"old_value": old_val, "new_value": new_values[field]}
            if changes:
                await self._record_audit(
                    station_id=station_id,
                    actor_user_id=actor_user_id,
                    action=AuditActionType.AUTO_BLOCK_SETTINGS_UPDATED,
                    details={"changes": changes},
                )

        await self.db.commit()

        logger.info(
            "הגדרות חסימה אוטומטית עודכנו",
            extra_data={
                "station_id": station_id,
                "auto_block_enabled": station.auto_block_enabled,
                "auto_block_grace_months": station.auto_block_grace_months,
                "auto_block_min_debt": station.auto_block_min_debt,
            }
        )
        return True, "הגדרות חסימה אוטומטית עודכנו בהצלחה."

    # ==================== שלב 5: חסימה אוטומטית ====================

    @staticmethod
    def _get_previous_billing_cycle_start(current_cycle: datetime | None = None) -> datetime:
        """חישוב תחילת מחזור החיוב הקודם (28 לחודש שלפני).

        מקבל current_cycle כדי למנוע חוסר עקביות בקריאות utcnow נפרדות.
        """
        if current_cycle is None:
            current_cycle = StationService.get_billing_cycle_start()
        if current_cycle.month == 1:
            return current_cycle.replace(year=current_cycle.year - 1, month=12)
        return current_cycle.replace(month=current_cycle.month - 1)

    async def auto_block_unpaid_drivers(
        self, station_id: int
    ) -> List[dict]:
        """חסימה אוטומטית של נהגים שלא שילמו במחזורי חיוב רצופים לתחנה.

        משתמש בהגדרות per-station:
        - auto_block_enabled: האם חסימה אוטומטית פעילה
        - auto_block_grace_months: מספר חודשים רצופים לפני חסימה (ברירת מחדל: 2)
        - auto_block_min_debt: סף חוב מינימלי לחסימה (ברירת מחדל: 0)

        רק חיובים עם courier_id ידוע נלקחים בחשבון.

        Returns:
            רשימת נהגים שנחסמו, כל אחד כ-dict עם courier_id ו-driver_name.
        """
        # שליפת הגדרות התחנה
        station = await self.get_station(station_id)
        if not station:
            return []

        # בדיקה אם חסימה אוטומטית מופעלת לתחנה
        if not station.auto_block_enabled:
            logger.info(
                "חסימה אוטומטית מושבתת לתחנה",
                extra_data={"station_id": station_id}
            )
            return []

        grace_months = station.auto_block_grace_months
        min_debt = station.auto_block_min_debt

        # חישוב תחילת כל מחזורי החיוב הרלוונטיים
        current_cycle_start = self.get_billing_cycle_start()
        cycle_starts = [current_cycle_start]
        for _ in range(grace_months - 1):
            cycle_starts.append(
                self._get_previous_billing_cycle_start(cycle_starts[-1])
            )
        # מיון מהמחזור הישן ביותר לנוכחי
        cycle_starts.reverse()
        oldest_cycle_start = cycle_starts[0]

        # שליפת כל החיובים שלא שולמו עם courier_id ידוע מתחילת המחזור הישן ביותר
        result = await self.db.execute(
            select(ManualCharge).where(
                ManualCharge.station_id == station_id,
                ManualCharge.courier_id.isnot(None),
                ManualCharge.is_paid == False,  # noqa: E712
                ManualCharge.created_at >= oldest_cycle_start,
            )
        )
        charges = list(result.scalars().all())

        if not charges:
            return []

        # קיבוץ לפי courier_id — בדיקת נוכחות בכל מחזור וחישוב סכום חוב
        courier_cycles: dict[int, set[int]] = {}
        courier_names: dict[int, str] = {}
        courier_debts: dict[int, Decimal] = {}
        for charge in charges:
            cid = charge.courier_id
            if cid not in courier_cycles:
                courier_cycles[cid] = set()
                courier_names[cid] = charge.driver_name
                courier_debts[cid] = Decimal("0")

            courier_debts[cid] += Decimal(str(charge.amount))

            # זיהוי לאיזה מחזור שייך החיוב
            for idx in range(len(cycle_starts)):
                cycle_end = cycle_starts[idx + 1] if idx + 1 < len(cycle_starts) else None
                if cycle_end is None:
                    # מחזור נוכחי — כל מה שאחרי cycle_starts[-1]
                    if charge.created_at >= cycle_starts[idx]:
                        courier_cycles[cid].add(idx)
                elif cycle_starts[idx] <= charge.created_at < cycle_end:
                    courier_cycles[cid].add(idx)

        # חסימת נהגים שמופיעים בכל grace_months מחזורים רצופים
        blocked_drivers: List[dict] = []
        for courier_id, cycles in courier_cycles.items():
            if len(cycles) < grace_months:
                continue  # לא מספיק מחזורים עם חוב

            # בדיקת סף חוב מינימלי
            if min_debt > 0 and courier_debts[courier_id] < min_debt:
                continue

            # בדיקה אם כבר חסום בתחנה
            if await self.is_blacklisted(station_id, courier_id):
                continue

            # שימוש ב-savepoint + IntegrityError fallback למניעת race condition
            # כשמשימות מקביליות מנסות לחסום אותו נהג בו-זמנית
            try:
                async with self.db.begin_nested():
                    entry = StationBlacklist(
                        station_id=station_id,
                        courier_id=courier_id,
                        reason=f"חסימה אוטומטית - אי תשלום {grace_months} חודשים רצופים",
                        consecutive_unpaid_months=grace_months,
                    )
                    self.db.add(entry)
                    await self.db.flush()
            except IntegrityError:
                # נהג כבר נחסם על ידי תהליך מקבילי — ממשיכים
                logger.info(
                    "נהג כבר חסום (race condition)",
                    extra_data={"station_id": station_id, "courier_id": courier_id}
                )
                continue

            blocked_drivers.append({
                "courier_id": courier_id,
                "driver_name": courier_names.get(courier_id, "לא ידוע"),
            })

            logger.info(
                "נהג נחסם אוטומטית בתחנה עקב אי תשלום",
                extra_data={
                    "station_id": station_id,
                    "courier_id": courier_id,
                    "grace_months": grace_months,
                    "total_debt": str(courier_debts[courier_id]),
                }
            )

        if blocked_drivers:
            await self.db.commit()

        return blocked_drivers

    # ==================== סעיף 9: דשבורד מולטי-תחנה ====================

    async def get_multi_station_summary(
        self,
        owner_id: int,
    ) -> list[dict]:
        """קבלת סיכום נתונים לכל התחנות שבבעלות המשתמש.

        מחזיר רשימת dict עם נתוני דשבורד לכל תחנה, ומסכום מצטבר.
        שאילתות batch — ללא N+1.
        """
        from sqlalchemy import func
        from app.db.models.station_ledger import StationLedgerEntryType
        from datetime import timezone
        from zoneinfo import ZoneInfo

        stations = await self.get_stations_by_owner(owner_id)
        if not stations:
            return []

        station_ids = [s.id for s in stations]

        # חישוב תחילת היום לפי שעון ישראל
        _ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
        now_israel = datetime.now(_ISRAEL_TZ)
        today_start = now_israel.replace(
            hour=0, minute=0, second=0, microsecond=0,
        ).astimezone(timezone.utc).replace(tzinfo=None)

        # --- שאילתת batch: ספירת משלוחים פעילים לכל תחנה ---
        active_counts_result = await self.db.execute(
            select(
                Delivery.station_id,
                func.count(Delivery.id),
            ).where(
                Delivery.station_id.in_(station_ids),
                Delivery.status.in_([
                    DeliveryStatus.OPEN,
                    DeliveryStatus.PENDING_APPROVAL,
                    DeliveryStatus.CAPTURED,
                    DeliveryStatus.IN_PROGRESS,
                ]),
            ).group_by(Delivery.station_id)
        )
        active_map: dict[int, int] = dict(active_counts_result.all())

        # --- שאילתת batch: משלוחים שנוצרו היום לכל תחנה ---
        today_counts_result = await self.db.execute(
            select(
                Delivery.station_id,
                func.count(Delivery.id),
            ).where(
                Delivery.station_id.in_(station_ids),
                Delivery.created_at >= today_start,
            ).group_by(Delivery.station_id)
        )
        today_map: dict[int, int] = dict(today_counts_result.all())

        # --- שאילתת batch: משלוחים שנמסרו היום לכל תחנה ---
        delivered_counts_result = await self.db.execute(
            select(
                Delivery.station_id,
                func.count(Delivery.id),
            ).where(
                Delivery.station_id.in_(station_ids),
                Delivery.status == DeliveryStatus.DELIVERED,
                Delivery.delivered_at >= today_start,
            ).group_by(Delivery.station_id)
        )
        delivered_map: dict[int, int] = dict(delivered_counts_result.all())

        # --- שאילתת batch: ארנקים ---
        wallets_result = await self.db.execute(
            select(StationWallet).where(
                StationWallet.station_id.in_(station_ids),
            )
        )
        wallets_map: dict[int, StationWallet] = {
            w.station_id: w for w in wallets_result.scalars().all()
        }

        # --- שאילתת batch: הכנסות היום לכל תחנה ---
        revenue_result = await self.db.execute(
            select(
                StationLedger.station_id,
                func.coalesce(func.sum(StationLedger.amount), 0),
            ).where(
                StationLedger.station_id.in_(station_ids),
                StationLedger.created_at >= today_start,
                StationLedger.entry_type.in_([
                    StationLedgerEntryType.COMMISSION_CREDIT,
                    StationLedgerEntryType.MANUAL_CHARGE,
                ]),
            ).group_by(StationLedger.station_id)
        )
        revenue_map: dict[int, float] = {
            row[0]: float(row[1]) for row in revenue_result.all()
        }

        # --- שאילתת batch: סדרנים פעילים לכל תחנה ---
        dispatchers_result = await self.db.execute(
            select(
                StationDispatcher.station_id,
                func.count(StationDispatcher.id),
            ).where(
                StationDispatcher.station_id.in_(station_ids),
                StationDispatcher.is_active == True,  # noqa: E712
            ).group_by(StationDispatcher.station_id)
        )
        dispatchers_map: dict[int, int] = dict(dispatchers_result.all())

        # --- שאילתת batch: חסומים לכל תחנה ---
        blacklisted_result = await self.db.execute(
            select(
                StationBlacklist.station_id,
                func.count(StationBlacklist.id),
            ).where(
                StationBlacklist.station_id.in_(station_ids),
            ).group_by(StationBlacklist.station_id)
        )
        blacklisted_map: dict[int, int] = dict(blacklisted_result.all())

        # --- הרכבת התוצאה ---
        summaries: list[dict] = []
        for station in stations:
            sid = station.id
            wallet = wallets_map.get(sid)
            summaries.append({
                "station_id": sid,
                "station_name": station.name,
                "active_deliveries_count": active_map.get(sid, 0),
                "today_deliveries_count": today_map.get(sid, 0),
                "today_delivered_count": delivered_map.get(sid, 0),
                "wallet_balance": float(wallet.balance) if wallet else 0.0,
                "commission_rate": float(wallet.commission_rate) if wallet else 0.1,
                "today_revenue": revenue_map.get(sid, 0.0),
                "active_dispatchers_count": dispatchers_map.get(sid, 0),
                "blacklisted_count": blacklisted_map.get(sid, 0),
            })

        return summaries
