"""
Capture Service - Atomic Delivery Capture with Credit Debit

Implements the atomic capture + credit debit pattern using PostgreSQL
row locks to ensure data consistency.
"""
from datetime import datetime
from typing import Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.db.models.delivery import Delivery, DeliveryStatus
from app.db.models.courier_wallet import CourierWallet
from app.db.models.wallet_ledger import WalletLedger, LedgerEntryType
from app.domain.services.outbox_service import OutboxService


class CaptureError(Exception):
    """Custom exception for capture failures"""
    pass


class CaptureService:
    """
    Service for atomic delivery capture.

    Implements the following atomic operation:
    1. Lock delivery record (SELECT ... FOR UPDATE)
    2. Verify status is OPEN
    3. Lock courier wallet (SELECT ... FOR UPDATE)
    4. Calculate future balance; reject if below credit limit
    5. Update delivery to CAPTURED
    6. Insert ledger entry (DELIVERY_FEE_DEBIT)
    7. Commit or rollback atomically
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.outbox_service = OutboxService(db)

    async def capture_delivery_by_token(
        self,
        token: str,
        courier_id: int
    ) -> Tuple[bool, str, Optional[Delivery]]:
        """
        Capture a delivery using secure token (for smart links).

        This is the preferred method as it prevents ID guessing attacks.
        """
        # שליפת משלוח לפי טוקן
        result = await self.db.execute(
            select(Delivery).where(Delivery.token == token)
        )
        delivery = result.scalar_one_or_none()

        if not delivery:
            return False, "המשלוח לא נמצא (קישור לא תקין)", None

        # שלב 4: משלוח של תחנה עובר דרך זרימת אישור
        if delivery.station_id:
            from app.domain.services.shipment_workflow_service import ShipmentWorkflowService
            workflow = ShipmentWorkflowService(self.db)
            return await workflow.request_delivery(delivery.id, courier_id)

        # משלוח ישיר (ללא תחנה) - תפיסה ישירה
        return await self.capture_delivery(delivery.id, courier_id)

    async def capture_delivery(
        self,
        delivery_id: int,
        courier_id: int
    ) -> Tuple[bool, str, Optional[Delivery]]:
        """
        Atomically capture a delivery for a courier.

        Returns:
            Tuple of (success, message, delivery)
        """
        try:
            # Start atomic operation
            # 1. Lock delivery record
            delivery_result = await self.db.execute(
                select(Delivery)
                .where(Delivery.id == delivery_id)
                .with_for_update()
            )
            delivery = delivery_result.scalar_one_or_none()

            if not delivery:
                return False, "המשלוח לא נמצא", None

            # 2. Verify status is OPEN or PENDING_APPROVAL (שלב 4: אחרי אישור סדרן)
            if delivery.status not in (
                DeliveryStatus.OPEN, DeliveryStatus.PENDING_APPROVAL
            ):
                return False, "המשלוח כבר נתפס על ידי שליח אחר", None

            # שלב 4: אם הסטטוס PENDING_APPROVAL — לוודא שהשליח הוא מי שביקש
            if delivery.status == DeliveryStatus.PENDING_APPROVAL:
                if delivery.requesting_courier_id != courier_id:
                    return False, "המשלוח ממתין לאישור עבור שליח אחר", None

            # 3. Lock courier wallet
            wallet_result = await self.db.execute(
                select(CourierWallet)
                .where(CourierWallet.courier_id == courier_id)
                .with_for_update()
            )
            wallet = wallet_result.scalar_one_or_none()

            if not wallet:
                # Create wallet if doesn't exist
                wallet = CourierWallet(
                    courier_id=courier_id,
                    balance=0.0,
                    credit_limit=-100.0
                )
                self.db.add(wallet)
                await self.db.flush()

            # 4. Calculate future balance and check credit limit
            fee = delivery.fee
            future_balance = wallet.balance - fee

            if future_balance < wallet.credit_limit:
                return False, f"יתרה לא מספיקה. יתרה נוכחית: {wallet.balance}₪, עמלה: {fee}₪, מגבלת אשראי: {wallet.credit_limit}₪", None

            # 5. Update delivery to CAPTURED
            delivery.status = DeliveryStatus.CAPTURED
            delivery.courier_id = courier_id
            delivery.captured_at = datetime.utcnow()

            # 6. Update wallet balance
            wallet.balance = future_balance

            # 7. Insert ledger entry (with unique constraint preventing double-debit)
            ledger_entry = WalletLedger(
                courier_id=courier_id,
                delivery_id=delivery_id,
                entry_type=LedgerEntryType.DELIVERY_FEE_DEBIT,
                amount=-fee,
                balance_after=future_balance,
                description=f"עמלה עבור תפיסת משלוח #{delivery_id}"
            )
            self.db.add(ledger_entry)

            # Queue notification messages via outbox
            await self.outbox_service.queue_capture_notification(delivery, courier_id)

            # 8. Commit transaction
            await self.db.commit()
            await self.db.refresh(delivery)

            return True, f"המשלוח נתפס בהצלחה! עמלה: {fee}₪, יתרה חדשה: {future_balance}₪", delivery

        except Exception as e:
            await self.db.rollback()
            raise CaptureError(f"שגיאה בתפיסת המשלוח: {str(e)}")

    async def release_delivery(
        self,
        delivery_id: int,
        courier_id: int
    ) -> Tuple[bool, str]:
        """
        Release a captured delivery (return it to open status).
        Refunds the fee to the courier.
        """
        try:
            # Lock delivery
            delivery_result = await self.db.execute(
                select(Delivery)
                .where(Delivery.id == delivery_id)
                .with_for_update()
            )
            delivery = delivery_result.scalar_one_or_none()

            if not delivery:
                return False, "המשלוח לא נמצא"

            if delivery.courier_id != courier_id:
                return False, "המשלוח לא שייך לך"

            if delivery.status != DeliveryStatus.CAPTURED:
                return False, "לא ניתן לשחרר משלוח שאינו בסטטוס 'נתפס'"

            # Lock wallet
            wallet_result = await self.db.execute(
                select(CourierWallet)
                .where(CourierWallet.courier_id == courier_id)
                .with_for_update()
            )
            wallet = wallet_result.scalar_one_or_none()

            if wallet:
                # Refund the fee
                fee = delivery.fee
                wallet.balance += fee

                # Add refund ledger entry
                ledger_entry = WalletLedger(
                    courier_id=courier_id,
                    delivery_id=delivery_id,
                    entry_type=LedgerEntryType.REFUND,
                    amount=fee,
                    balance_after=wallet.balance,
                    description=f"החזר עמלה עבור שחרור משלוח #{delivery_id}"
                )
                self.db.add(ledger_entry)

            # Return delivery to open status
            delivery.status = DeliveryStatus.OPEN
            delivery.courier_id = None
            delivery.captured_at = None

            await self.db.commit()

            return True, "המשלוח שוחרר בהצלחה והעמלה הוחזרה"

        except Exception as e:
            await self.db.rollback()
            raise CaptureError(f"שגיאה בשחרור המשלוח: {str(e)}")
