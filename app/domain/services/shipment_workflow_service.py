"""
Shipment Workflow Service - זרימת אישור משלוח [שלב 4]

מתזמר את הזרימה:
1. שליח לוחץ על קישור חכם → בדיקת תקינות (אישור + blacklist)
2. בקשה נשלחת לסדרני התחנה
3. סדרן מאשר/דוחה → כרטיס סגור נשלח לקבוצה פרטית
"""
from datetime import datetime, timezone
from typing import Tuple, Optional
from html import escape

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.user import User, UserRole, ApprovalStatus
from app.db.models.station import Station
from app.db.models.audit_log import AuditActionType
from app.domain.services.station_service import StationService
from app.domain.services.capture_service import CaptureService
from app.domain.services.outbox_service import OutboxService
from app.domain.services.audit_service import AuditService
from app.domain.services.alert_service import publish_delivery_captured
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator

logger = get_logger(__name__)


class ShipmentWorkflowService:
    """שירות זרימת אישור משלוח - שלב 4"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.station_service = StationService(db)
        self.capture_service = CaptureService(db)
        self.outbox_service = OutboxService(db)
        self.audit_service = AuditService(db)

    async def request_delivery(
        self, delivery_id: int, courier_id: int
    ) -> Tuple[bool, str, Optional[Delivery]]:
        """
        בקשת משלוח על ידי שליח — שלב ראשון בזרימת האישור.

        1. נעילת שורה למניעת race condition
        2. אימות סטטוס OPEN + שליח מאושר + לא חסום בתחנה
        3. עדכון סטטוס ל-PENDING_APPROVAL
        4. שליחת הודעה לסדרנים
        """
        # נעילת שורת משלוח למניעת בקשות מקבילות
        result = await self.db.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .with_for_update()
        )
        delivery = result.scalar_one_or_none()

        if not delivery:
            return False, "המשלוח לא נמצא.", None

        if delivery.status != DeliveryStatus.OPEN:
            if delivery.status == DeliveryStatus.PENDING_APPROVAL:
                return False, "כבר הוגשה בקשה למשלוח זה. ממתין לאישור סדרן.", None
            return False, "המשלוח כבר נתפס על ידי שליח אחר.", None

        # אימות שליח
        courier_result = await self.db.execute(
            select(User).where(User.id == courier_id)
        )
        courier = courier_result.scalar_one_or_none()

        if not courier:
            return False, "שליח לא נמצא.", None

        if courier.approval_status != ApprovalStatus.APPROVED:
            logger.warning(
                "Unapproved courier tried to request delivery",
                extra_data={
                    "courier_id": courier_id,
                    "delivery_id": delivery_id,
                    "approval_status": str(courier.approval_status),
                }
            )
            return False, "אין לך הרשאה לקחת משלוחים. יש לחכות לאישור מנהל.", None

        # בדיקת blacklist ברמת תחנה
        if delivery.station_id:
            is_blocked = await self.station_service.is_blacklisted(
                delivery.station_id, courier_id
            )
            if is_blocked:
                logger.info(
                    "Blacklisted courier tried to request station delivery",
                    extra_data={
                        "courier_id": courier_id,
                        "station_id": delivery.station_id,
                        "delivery_id": delivery_id,
                    }
                )
                return False, "אינך מורשה לקחת משלוחים מתחנה זו.", None

        # עדכון סטטוס ל-PENDING_APPROVAL
        delivery.status = DeliveryStatus.PENDING_APPROVAL
        delivery.requesting_courier_id = courier_id
        delivery.requested_at = datetime.now(timezone.utc)
        # ניקוי שדות אישור ישנים (רלוונטי אחרי דחייה + בקשה חוזרת)
        delivery.approved_by_id = None
        delivery.approved_at = None
        delivery.approval_decision = None

        # שליחת הודעה לסדרנים עם כפתורי אישור/דחייה
        if delivery.station_id:
            await self.outbox_service.queue_delivery_request_to_dispatchers(
                delivery, courier, delivery.station_id
            )

        # רישום בקשת משלוח בלוג ביקורת
        await self.audit_service.record(
            actor_user_id=courier_id,
            action=AuditActionType.DELIVERY_REQUESTED,
            station_id=delivery.station_id,
            entity_type="delivery",
            entity_id=delivery_id,
            old_value={"status": DeliveryStatus.OPEN.value},
            new_value={"status": DeliveryStatus.PENDING_APPROVAL.value},
            details={"courier_id": courier_id},
        )

        await self.db.commit()
        await self.db.refresh(delivery)

        logger.info(
            "Delivery request submitted for approval",
            extra_data={
                "delivery_id": delivery_id,
                "courier_id": courier_id,
                "station_id": delivery.station_id,
            }
        )
        return True, "✅ בקשתך נשלחה לסדרני התחנה לאישור. תקבל הודעה כשתתקבל החלטה.", delivery

    async def approve_delivery(
        self, delivery_id: int, dispatcher_id: int
    ) -> Tuple[bool, str, Optional[Delivery]]:
        """
        אישור משלוח על ידי סדרן — מפעיל תפיסה אטומית + כרטיס סגור.
        """
        # נעילת שורה
        result = await self.db.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .with_for_update()
        )
        delivery = result.scalar_one_or_none()

        if not delivery:
            return False, "המשלוח לא נמצא.", None

        if delivery.status != DeliveryStatus.PENDING_APPROVAL:
            return False, "המשלוח לא ממתין לאישור.", None

        if not delivery.requesting_courier_id:
            return False, "אין שליח מבקש למשלוח זה.", None

        # אימות שהסדרן שייך לתחנה הנכונה (is_dispatcher_of_station תומך בסדרן מרובה-תחנות)
        if delivery.station_id:
            is_disp = await self.station_service.is_dispatcher_of_station(
                dispatcher_id, delivery.station_id
            )
            if not is_disp:
                return False, "אין לך הרשאה לאשר משלוחים בתחנה זו.", None

        # אימות מחדש שהשליח עדיין מאושר ולא חסום (עלול להשתנות בין בקשה לאישור)
        courier_id = delivery.requesting_courier_id
        courier_check = await self._get_user(courier_id)
        if not courier_check or courier_check.approval_status != ApprovalStatus.APPROVED:
            logger.warning(
                "Courier no longer approved at approval time",
                extra_data={"courier_id": courier_id, "delivery_id": delivery_id}
            )
            return False, "❌ השליח כבר לא מאושר לקחת משלוחים.", None

        if delivery.station_id:
            is_blocked = await self.station_service.is_blacklisted(
                delivery.station_id, courier_id
            )
            if is_blocked:
                logger.warning(
                    "Courier blacklisted at approval time",
                    extra_data={
                        "courier_id": courier_id,
                        "station_id": delivery.station_id,
                        "delivery_id": delivery_id,
                    }
                )
                return False, "❌ השליח חסום בתחנה זו.", None

        # ביצוע תפיסה אטומית דרך CaptureService (חיוב ארנק וכו')
        # auto_commit=False כדי שהכל יהיה באותה טרנזקציה עם שדות האישור
        success, msg, captured = await self.capture_service.capture_delivery(
            delivery_id, courier_id, auto_commit=False
        )

        if not success:
            # rollback למניעת שינויים חלקיים (למשל ארנק חדש שנוצר ב-flush)
            await self.db.rollback()
            # לוג עם פרטים מלאים — לא חושפים יתרת ארנק לסדרן
            logger.warning(
                "Delivery capture failed during approval",
                extra_data={
                    "delivery_id": delivery_id,
                    "courier_id": courier_id,
                    "dispatcher_id": dispatcher_id,
                    "capture_error": msg,
                }
            )
            return False, "❌ לא ניתן לאשר את המשלוח כרגע. יש לבדוק את יתרת השליח.", None

        # עדכון שדות אישור — באותה טרנזקציה עם התפיסה וחיוב הארנק
        await self.db.refresh(delivery)
        delivery.approved_by_id = dispatcher_id
        delivery.approved_at = datetime.now(timezone.utc)
        delivery.approval_decision = "approved"

        # שליפת שני משתמשים בשאילתה אחת במקום שתיים נפרדות
        courier, dispatcher = await self._get_users_batch(courier_id, dispatcher_id)

        if courier:
            await self._notify_courier_approved(delivery, courier)

        if delivery.station_id and courier and dispatcher:
            await self._send_closed_card(
                delivery, courier, "approved", dispatcher
            )

        # רישום אישור משלוח בלוג ביקורת
        await self.audit_service.record(
            actor_user_id=dispatcher_id,
            action=AuditActionType.DELIVERY_APPROVED,
            station_id=delivery.station_id,
            target_user_id=courier_id,
            entity_type="delivery",
            entity_id=delivery_id,
            old_value={"status": DeliveryStatus.PENDING_APPROVAL.value},
            new_value={"status": DeliveryStatus.CAPTURED.value},
            details={
                "dispatcher_id": dispatcher_id,
                "courier_id": courier_id,
            },
        )

        # commit אחד אטומי: תפיסה + חיוב + אישור + הודעות outbox
        await self.db.commit()
        await self.db.refresh(delivery)

        # התראה בזמן אמת לפאנל — אחרי commit מוצלח
        # מחוץ לזרימה העסקית כדי שכשלון התראה לא ישפיע על תוצאת הפעולה
        if delivery.station_id and courier:
            try:
                courier_name = courier.full_name or courier.name or "לא צוין"
                await publish_delivery_captured(
                    station_id=delivery.station_id,
                    delivery_id=delivery.id,
                    courier_name=courier_name,
                )
            except Exception as e:
                logger.error(
                    "כשלון בפרסום התראת אישור משלוח — הפעולה העסקית הצליחה",
                    extra_data={
                        "delivery_id": delivery_id,
                        "courier_id": courier_id,
                        "error": str(e),
                    },
                    exc_info=True,
                )

        logger.info(
            "Delivery approved by dispatcher",
            extra_data={
                "delivery_id": delivery_id,
                "courier_id": courier_id,
                "dispatcher_id": dispatcher_id,
            }
        )
        return True, f"✅ המשלוח אושר ונשלח לנהג.", delivery

    async def reject_delivery(
        self, delivery_id: int, dispatcher_id: int
    ) -> Tuple[bool, str, Optional[Delivery]]:
        """
        דחיית משלוח על ידי סדרן — החזרת סטטוס ל-OPEN + כרטיס סגור.
        """
        # נעילת שורה
        result = await self.db.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .with_for_update()
        )
        delivery = result.scalar_one_or_none()

        if not delivery:
            return False, "המשלוח לא נמצא.", None

        if delivery.status != DeliveryStatus.PENDING_APPROVAL:
            return False, "המשלוח לא ממתין לאישור.", None

        if not delivery.requesting_courier_id:
            return False, "אין שליח מבקש למשלוח זה.", None

        # אימות שהסדרן שייך לתחנה (is_dispatcher_of_station תומך בסדרן מרובה-תחנות)
        if delivery.station_id:
            is_disp = await self.station_service.is_dispatcher_of_station(
                dispatcher_id, delivery.station_id
            )
            if not is_disp:
                return False, "אין לך הרשאה לדחות משלוחים בתחנה זו.", None

        # שמירת מזהה השליח לפני ניקוי
        courier_id = delivery.requesting_courier_id

        # החזרת סטטוס ל-OPEN
        delivery.status = DeliveryStatus.OPEN
        delivery.requesting_courier_id = None
        delivery.requested_at = None
        delivery.approved_by_id = dispatcher_id
        delivery.approved_at = datetime.now(timezone.utc)
        delivery.approval_decision = "rejected"

        # שליפת שני משתמשים בשאילתה אחת במקום שתיים נפרדות
        courier, dispatcher = await self._get_users_batch(courier_id, dispatcher_id)

        if courier:
            await self._notify_courier_rejected(delivery, courier)

        if delivery.station_id and courier and dispatcher:
            await self._send_closed_card(
                delivery, courier, "rejected", dispatcher
            )

        # רישום דחיית משלוח בלוג ביקורת
        await self.audit_service.record(
            actor_user_id=dispatcher_id,
            action=AuditActionType.DELIVERY_REJECTED,
            station_id=delivery.station_id,
            target_user_id=courier_id,
            entity_type="delivery",
            entity_id=delivery_id,
            old_value={"status": DeliveryStatus.PENDING_APPROVAL.value},
            new_value={"status": DeliveryStatus.OPEN.value},
            details={
                "dispatcher_id": dispatcher_id,
                "courier_id": courier_id,
            },
        )

        # commit אחד אטומי: שינוי מצב + הודעות outbox
        await self.db.commit()
        await self.db.refresh(delivery)

        logger.info(
            "Delivery rejected by dispatcher",
            extra_data={
                "delivery_id": delivery_id,
                "courier_id": courier_id,
                "dispatcher_id": dispatcher_id,
            }
        )
        return True, f"❌ המשלוח נדחה. הוא חזר לסטטוס פתוח.", delivery

    # ==================== כרטיס סגור ====================

    @staticmethod
    def format_closed_card(
        delivery: Delivery,
        courier: User,
        decision: str,
        dispatcher: User,
    ) -> str:
        """פורמט כרטיס משלוח סגור — HTML"""
        status_text = "נשלח לנהג ✅" if decision == "approved" else "נדחה ❌"
        vehicle_display = {
            "car_4": "רכב 4 מקומות",
            "car_7": "7 מקומות",
            "pickup_truck": "טנדר",
            "motorcycle": "אופנוע",
        }.get(courier.vehicle_category or "", escape(courier.vehicle_category or "לא צוין"))

        courier_name = escape(
            courier.full_name or courier.name or "לא צוין"
        )
        dispatcher_name = escape(
            dispatcher.full_name or dispatcher.name or "לא צוין"
        )

        return (
            "🔒 <b>כרטיס משלוח סגור</b>\n\n"
            "📦 <b>פרטי המשלוח:</b>\n"
            f"• מספר: #{delivery.id}\n"
            f"• תיאור: {escape(delivery.dropoff_notes or 'לא צוין')}\n"
            f"• זמן יצירה: {delivery.created_at.strftime('%d/%m/%Y %H:%M')}\n"
            f"• איסוף: {escape(delivery.pickup_address)}\n"
            f"• יעד: {escape(delivery.dropoff_address)}\n"
            f"• עמלה: {delivery.fee:.0f} ₪\n\n"
            "🚚 <b>פרטי הנהג:</b>\n"
            f"• שם מלא: {courier_name}\n"
            f"• טלפון: {PhoneNumberValidator.mask(courier.phone_number) if courier.phone_number else 'לא צוין'}\n"
            f"• סוג רכב: {vehicle_display}\n\n"
            f"📌 <b>סטטוס:</b> {status_text}\n"
            f"👤 <b>סדרן:</b> {dispatcher_name}"
        )

    # ==================== עזר פנימי ====================

    async def _get_user(self, user_id: int) -> Optional[User]:
        """שליפת משתמש לפי מזהה"""
        if not user_id:
            return None
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def _get_users_batch(
        self, *user_ids: int
    ) -> tuple[Optional[User], ...]:
        """שליפת מספר משתמשים בשאילתה אחת — חוסך round-trip ל-DB"""
        valid_ids = [uid for uid in user_ids if uid]
        if not valid_ids:
            return tuple(None for _ in user_ids)

        result = await self.db.execute(
            select(User).where(User.id.in_(valid_ids))
        )
        users_by_id: dict[int, User] = {u.id: u for u in result.scalars().all()}
        return tuple(users_by_id.get(uid) for uid in user_ids)

    async def _notify_courier_approved(
        self, delivery: Delivery, courier: User
    ) -> None:
        """הודעה לשליח שהמשלוח אושר — כולל פרטים מלאים"""
        message_text = (
            "✅ <b>המשלוח אושר!</b>\n\n"
            f"📦 משלוח #{delivery.id}\n"
            f"📍 איסוף: {escape(delivery.pickup_address)}\n"
        )
        if delivery.pickup_contact_name:
            message_text += f"👤 איש קשר: {escape(delivery.pickup_contact_name)}\n"
        if delivery.pickup_contact_phone:
            message_text += f"📞 טלפון: {escape(delivery.pickup_contact_phone)}\n"
        if delivery.pickup_notes:
            message_text += f"📝 הערות: {escape(delivery.pickup_notes)}\n"

        message_text += f"\n🎯 יעד: {escape(delivery.dropoff_address)}\n"
        if delivery.dropoff_contact_name:
            message_text += f"👤 איש קשר: {escape(delivery.dropoff_contact_name)}\n"
        if delivery.dropoff_contact_phone:
            message_text += f"📞 טלפון: {escape(delivery.dropoff_contact_phone)}\n"
        if delivery.dropoff_notes:
            message_text += f"📝 הערות: {escape(delivery.dropoff_notes)}\n"

        message_text += f"\n💰 עמלה: {delivery.fee:.0f} ₪"

        await self.outbox_service.queue_delivery_decision_notification(
            delivery, courier, message_text
        )

    async def _notify_courier_rejected(
        self, delivery: Delivery, courier: User
    ) -> None:
        """הודעה לשליח שהבקשה נדחתה"""
        message_text = (
            f"❌ <b>בקשתך למשלוח #{delivery.id} נדחתה</b>\n\n"
            "המשלוח חזר לסטטוס פתוח וזמין לשליחים אחרים."
        )
        await self.outbox_service.queue_delivery_decision_notification(
            delivery, courier, message_text
        )

    async def _send_closed_card(
        self,
        delivery: Delivery,
        courier: User,
        decision: str,
        dispatcher: User,
    ) -> None:
        """שליחת כרטיס סגור לקבוצה פרטית של התחנה"""
        station = await self.station_service.get_station(delivery.station_id)
        if not station or not station.private_group_chat_id:
            logger.info(
                "No private group configured for station, skipping closed card",
                extra_data={"station_id": delivery.station_id}
            )
            return

        card_text = self.format_closed_card(
            delivery, courier, decision, dispatcher
        )
        await self.outbox_service.queue_closed_card(
            station, card_text
        )
